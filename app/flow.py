"""Staged intake conversation.

Instead of one agent holding a long prompt for the whole call, the intake is broken into
short stages that hand control to one another. Each stage knows only its own slice of the
form and carries just the tools it needs. Collected values accumulate in a single
``IntakeState`` object that rides along on the session's ``userdata``.

Stage order:  Welcome -> Identity -> Contact -> Extras -> Review
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date

from livekit.agents import Agent, RunContext, function_tool

from app import metrics
from app.schema import (
    NewPatient,
    PatientChanges,
    canonical_email,
    canonical_sex,
    canonical_state,
    canonical_zip,
    parse_birth_date,
    reject_future_date,
    tidy_name,
    to_national_phone,
)
from app.store import PatientStore

# The worker sets ctx.log_context_fields = {"room": ...} once per call (app/worker.py), which
# LiveKit injects into every log record emitted during that job regardless of which logger it
# came from — so a plain stdlib logger here still gets "room" attached automatically, which is
# what makes a saved record traceable back to the specific call that produced it (see
# save_record below) without needing a schema change or a bespoke correlation mechanism.
log = logging.getLogger("intake.flow")

# --- Shared session state --------------------------------------------------


@dataclass
class IntakeState:
    given_name: str | None = None
    family_name: str | None = None
    birth_date: date | None = None
    sex: str | None = None
    phone: str | None = None
    street: str | None = None
    unit: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    email: str | None = None
    insurer: str | None = None
    member_id: str | None = None
    language: str = "English"
    contact_name: str | None = None
    contact_phone: str | None = None

    # Set when the caller matches a record we already hold.
    existing_record_id: str | None = None
    updating: bool = False

    def collected(self) -> dict:
        """Non-empty collected fields, keyed as the schema/store expect them."""
        keys = (
            "given_name",
            "family_name",
            "birth_date",
            "sex",
            "phone",
            "street",
            "unit",
            "city",
            "state",
            "postal_code",
            "email",
            "insurer",
            "member_id",
            "language",
            "contact_name",
            "contact_phone",
        )
        return {k: getattr(self, k) for k in keys if getattr(self, k) is not None}


def _spoken_lines(state: IntakeState) -> list[str]:
    """Natural-language sentences for the review readback, one per collected field.

    Kept as short, separate sentences (rather than one comma-joined line) so each can be
    spoken as its own TTS utterance with a deliberate pause after it — see ReviewStage.on_enter.
    """
    address = ", ".join(
        part
        for part in (
            state.street,
            state.unit,
            f"{state.city}, {state.state} {state.postal_code}",
        )
        if part
    )
    lines = [
        f"Your name is {state.given_name} {state.family_name}.",
        "Your date of birth is "
        f"{state.birth_date.isoformat() if state.birth_date else 'missing'}.",
        f"Your sex is {state.sex}.",
        f"Your phone number is {state.phone}.",
        f"Your address is {address}.",
    ]
    if state.email:
        lines.append(f"Your email is {state.email}.")
    if state.insurer:
        member = f", member id {state.member_id}," if state.member_id else ""
        lines.append(f"Your insurance is {state.insurer}{member}.")
    if state.contact_name:
        lines.append(f"Your emergency contact is {state.contact_name} at {state.contact_phone}.")
    lines.append(f"Your preferred language is {state.language}.")
    return lines


# --- Base stage ------------------------------------------------------------


class IntakeStage(Agent):
    """Common helper for reading the shared state from within a stage."""

    @property
    def state(self) -> IntakeState:
        return self.session.userdata


# --- 1. Welcome ------------------------------------------------------------


class WelcomeStage(IntakeStage):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You open a phone call for a medical front desk. Keep it to one or two short "
                "sentences: say hello, mention you'll take down some registration details, and "
                "ask if it's a good time to start. When the caller signals they're ready, call "
                "the begin_collection tool. Do not collect any details yourself."
            )
        )

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions=(
                "Warmly greet the caller, say you'll gather a few registration details, and ask "
                "whether they're ready to begin."
            )
        )

    @function_tool()
    async def begin_collection(self, context: RunContext) -> Agent:
        """Move on to collecting the caller's identity once they're ready."""
        return IdentityStage(chat_ctx=self.chat_ctx)


# --- 2. Identity -----------------------------------------------------------


class IdentityStage(IntakeStage):
    def __init__(self, *, chat_ctx=None) -> None:
        super().__init__(
            instructions=(
                "Collect the caller's legal first name, last name, date of birth, and sex. Ask "
                "conversationally, one item at a time, and accept them in whatever order they "
                "come. When you have all four, call submit_identity. If a value is rejected, tell "
                "the caller what was wrong and ask again only for that one item."
            ),
            chat_ctx=chat_ctx,
        )

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions="Ask the caller for their first and last name."
        )

    @function_tool()
    async def submit_identity(
        self,
        context: RunContext,
        first_name: str,
        last_name: str,
        date_of_birth: str,
        sex: str,
    ) -> dict | Agent:
        """Validate and store the caller's name, date of birth, and sex, then continue."""
        problems: dict[str, str] = {}
        try:
            given = tidy_name(first_name)
        except ValueError as exc:
            problems["first_name"] = str(exc)
        try:
            family = tidy_name(last_name)
        except ValueError as exc:
            problems["last_name"] = str(exc)
        try:
            dob = reject_future_date(parse_birth_date(date_of_birth))
        except ValueError as exc:
            problems["date_of_birth"] = str(exc)
        try:
            sex_value = canonical_sex(sex)
        except ValueError as exc:
            problems["sex"] = str(exc)

        if problems:
            return {"ok": False, "reprompt": problems}

        self.state.given_name = given
        self.state.family_name = family
        self.state.birth_date = dob
        self.state.sex = sex_value
        return ContactStage(chat_ctx=self.chat_ctx)


# --- 3. Contact ------------------------------------------------------------


class ContactStage(IntakeStage):
    def __init__(self, *, chat_ctx=None) -> None:
        super().__init__(
            instructions=(
                "First collect a US phone number and immediately call check_returning with it — "
                "this tells you whether we already have a record. Share that result naturally. "
                "Then collect the mailing address: street, optional unit or apartment, city, "
                "two-letter state, and ZIP. When the address is complete, call submit_address. "
                "Re-ask only for any field that fails validation."
            ),
            chat_ctx=chat_ctx,
        )

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions="Ask the caller for the best phone number to reach them."
        )

    @function_tool()
    async def check_returning(self, context: RunContext, phone: str) -> dict:
        """Store the caller's phone number and check whether a record already exists."""
        try:
            national = to_national_phone(phone)
        except ValueError as exc:
            return {"ok": False, "reprompt": {"phone": str(exc)}}

        self.state.phone = national
        with PatientStore.open() as store:
            existing = store.find_by_phone(national)
        if existing is None:
            return {"ok": True, "returning": False}

        self.state.existing_record_id = existing.record_id
        self.state.updating = True
        return {
            "ok": True,
            "returning": True,
            "given_name": existing.given_name,
            "family_name": existing.family_name,
        }

    @function_tool()
    async def submit_address(
        self,
        context: RunContext,
        street: str,
        city: str,
        state: str,
        postal_code: str,
        unit: str | None = None,
    ) -> dict | Agent:
        """Validate and store the mailing address, then move on to optional details."""
        if self.state.phone is None:
            return {"ok": False, "message": "Collect and check the phone number first."}

        problems: dict[str, str] = {}
        try:
            state_code = canonical_state(state)
        except ValueError as exc:
            problems["state"] = str(exc)
        try:
            zip_code = canonical_zip(postal_code)
        except ValueError as exc:
            problems["postal_code"] = str(exc)
        if not street.strip():
            problems["street"] = "street address cannot be empty"
        if not city.strip():
            problems["city"] = "city cannot be empty"

        if problems:
            return {"ok": False, "reprompt": problems}

        self.state.street = street.strip()
        self.state.unit = unit.strip() if unit and unit.strip() else None
        self.state.city = city.strip()
        self.state.state = state_code
        self.state.postal_code = zip_code
        return ExtrasStage(chat_ctx=self.chat_ctx)


# --- 4. Extras (optional) --------------------------------------------------


class ExtrasStage(IntakeStage):
    def __init__(self, *, chat_ctx=None) -> None:
        super().__init__(
            instructions=(
                "Offer four optional groups, briefly: insurance, an emergency contact, an email "
                "address, and a preferred language. Record whichever the caller wants using "
                "add_insurance, add_emergency_contact, add_email, or set_language — call the "
                "matching tool immediately whenever the caller volunteers one of these, even in "
                "passing or bundled with something else. Never state that a detail has been "
                "recorded without having called its tool first. If they decline everything or "
                "once they're done, call optional_done. Never insist."
            ),
            chat_ctx=chat_ctx,
        )

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions=(
                "Let the caller know you can also record insurance, an emergency contact, an "
                "email address, and a preferred language, and ask if they'd like to add any of "
                "those."
            )
        )

    @function_tool()
    async def add_insurance(
        self, context: RunContext, insurer: str, member_id: str | None = None
    ) -> dict:
        """Record the caller's insurance provider and optional member id."""
        if not insurer.strip():
            return {"ok": False, "reprompt": {"insurer": "insurer name cannot be empty"}}
        self.state.insurer = insurer.strip()
        self.state.member_id = member_id.strip() if member_id and member_id.strip() else None
        return {"ok": True}

    @function_tool()
    async def add_emergency_contact(
        self, context: RunContext, contact_name: str, contact_phone: str
    ) -> dict:
        """Record an emergency contact name and phone number."""
        problems: dict[str, str] = {}
        if not contact_name.strip():
            problems["contact_name"] = "contact name cannot be empty"
        try:
            national = to_national_phone(contact_phone)
        except ValueError as exc:
            problems["contact_phone"] = str(exc)
            national = None
        if problems:
            return {"ok": False, "reprompt": problems}
        self.state.contact_name = contact_name.strip()
        self.state.contact_phone = national
        return {"ok": True}

    @function_tool()
    async def add_email(self, context: RunContext, email: str) -> dict:
        """Record the caller's email address."""
        try:
            self.state.email = canonical_email(email)
        except ValueError as exc:
            return {"ok": False, "reprompt": {"email": str(exc)}}
        return {"ok": True}

    @function_tool()
    async def set_language(self, context: RunContext, language: str) -> dict:
        """Record the caller's preferred language."""
        if not language.strip():
            return {"ok": False, "reprompt": {"language": "language cannot be empty"}}
        self.state.language = language.strip()
        return {"ok": True}

    @function_tool()
    async def optional_done(self, context: RunContext) -> Agent:
        """Finish optional details and move to the final review."""
        return ReviewStage(chat_ctx=self.chat_ctx)


# --- 5. Review and save ----------------------------------------------------


class ReviewStage(IntakeStage):
    def __init__(self, *, chat_ctx=None) -> None:
        super().__init__(
            instructions=(
                "Read back the collected details and ask the caller to confirm they're correct. "
                "If they want a change — including something they mention was missed earlier in "
                "the call, like an email address — call amend with just the corrected fields, "
                "then read back again. Never state that a detail has been added, changed, or "
                "recorded unless you actually called amend with it first; do not just describe "
                "the correction in your reply. Only once they clearly confirm, call save_record. "
                "Never claim the record was saved unless save_record reports success."
            ),
            chat_ctx=chat_ctx,
        )

    async def on_enter(self) -> None:
        # Spoken directly line-by-line (rather than via generate_reply) so each field gets its
        # own TTS utterance with a real pause after it, instead of the LLM paraphrasing the
        # whole summary into one run-on, comma-chained sentence.
        await self.session.say("Let me read everything back to you.").wait_for_playout()
        await asyncio.sleep(0.4)
        for line in _spoken_lines(self.state):
            await self.session.say(line).wait_for_playout()
            await asyncio.sleep(0.4)

        await self.session.generate_reply(
            instructions=(
                "You already read every detail aloud in the prior turns. Do not repeat, list, or "
                "summarize any of those details again. Just ask one short question, like 'Does "
                "everything sound correct?'"
            )
        )

    @function_tool()
    async def amend(
        self,
        context: RunContext,
        first_name: str | None = None,
        last_name: str | None = None,
        date_of_birth: str | None = None,
        sex: str | None = None,
        phone: str | None = None,
        street: str | None = None,
        unit: str | None = None,
        city: str | None = None,
        state: str | None = None,
        postal_code: str | None = None,
        email: str | None = None,
        language: str | None = None,
    ) -> dict | None:
        """Correct one or more already-collected fields before saving."""
        problems: dict[str, str] = {}
        try:
            if first_name is not None:
                self.state.given_name = tidy_name(first_name)
            if last_name is not None:
                self.state.family_name = tidy_name(last_name)
            if date_of_birth is not None:
                self.state.birth_date = reject_future_date(parse_birth_date(date_of_birth))
            if sex is not None:
                self.state.sex = canonical_sex(sex)
            if phone is not None:
                self.state.phone = to_national_phone(phone)
            if state is not None:
                self.state.state = canonical_state(state)
            if postal_code is not None:
                self.state.postal_code = canonical_zip(postal_code)
            if email is not None:
                self.state.email = canonical_email(email)
        except ValueError as exc:
            problems["value"] = str(exc)
            return {"ok": False, "reprompt": problems}

        if street is not None:
            self.state.street = street.strip()
        if unit is not None:
            self.state.unit = unit.strip() or None
        if city is not None:
            self.state.city = city.strip()
        if language is not None:
            self.state.language = language.strip() or self.state.language

        # Same paced, line-by-line readback as on_enter (see the comment there) — a plain
        # dict return here would let the LLM paraphrase the correction into one run-on
        # sentence again, and returning None (rather than a dict) avoids the framework
        # firing a second, redundant auto-reply on top of the one triggered below.
        for line in _spoken_lines(self.state):
            await self.session.say(line).wait_for_playout()
            await asyncio.sleep(0.4)

        await self.session.generate_reply(
            instructions=(
                "You already read every detail aloud in the prior turns. Do not repeat, list, "
                "or summarize any of those details again. Just ask one short question, like "
                "'Does everything sound correct now?'"
            )
        )
        return None

    @function_tool()
    async def save_record(self, context: RunContext) -> dict:
        """Persist the confirmed patient record (creating or updating as appropriate)."""
        state = self.state
        try:
            if state.updating and state.existing_record_id:
                changes = PatientChanges(**state.collected())
                with PatientStore.open() as store:
                    row = store.get(state.existing_record_id)
                    if row is None:
                        log.error(
                            "save_record: existing record vanished",
                            extra={"record_id": state.existing_record_id},
                        )
                        metrics.call_completed(success=False)
                        return {"ok": False, "error": "existing record could not be found"}
                    row = store.apply_changes(row, changes)
                log.info(
                    "patient record updated",
                    extra={"record_id": row.record_id, "action": "updated"},
                )
                metrics.call_completed(success=True)
                return {"ok": True, "action": "updated", "record_id": row.record_id}

            new_patient = NewPatient(**state.collected())
            with PatientStore.open() as store:
                row = store.add(new_patient)
            # Mark as the caller's existing record so a later amend + re-save (e.g. a
            # post-confirmation correction) updates this row instead of inserting a duplicate.
            state.existing_record_id = row.record_id
            state.updating = True
            log.info(
                "patient record created", extra={"record_id": row.record_id, "action": "created"}
            )
            metrics.call_completed(success=True)
            return {"ok": True, "action": "created", "record_id": row.record_id}
        except Exception as exc:
            # Previously silent: the caller just heard a generic apology and the intake was
            # lost with zero trace. Now it lands in the room-tagged logs (see the `log`
            # module docstring above) so "what happened on call X" is actually answerable —
            # this is exactly the kind of failure a locked/unreachable DB produces.
            log.exception("save_record failed", extra={"updating": state.updating})
            metrics.call_completed(success=False)
            return {"ok": False, "error": str(exc)}
