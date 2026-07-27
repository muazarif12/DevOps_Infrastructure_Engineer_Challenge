"""Flow-level tests: the IntakeState -> schema/store contract used by save_record.

These exercise the data assembly without a live LiveKit session (the stage tools that read
`self.session.userdata` are covered indirectly here through the same state object).
"""

import datetime as dt

from app.flow import IntakeState, _spoken_lines
from app.schema import NewPatient, PatientChanges


def _full_state() -> IntakeState:
    return IntakeState(
        given_name="Katherine",
        family_name="Johnson",
        birth_date=dt.date(1918, 8, 26),
        sex="Female",
        phone="2127365000",
        street="100 Main St",
        city="Hampton",
        state="VA",
        postal_code="23666",
    )


def test_collected_omits_unset_optional_fields():
    collected = _full_state().collected()
    assert "email" not in collected
    assert "insurer" not in collected
    # language carries a default and is always present
    assert collected["language"] == "English"


def test_collected_state_builds_valid_new_patient():
    record = NewPatient(**_full_state().collected())
    assert record.given_name == "Katherine"
    assert record.state == "VA"
    assert record.phone == "2127365000"


def test_collected_state_builds_partial_changes():
    state = _full_state()
    state.city = "Newport News"
    changes = PatientChanges(**state.collected())
    dumped = changes.model_dump(exclude_unset=True)
    assert dumped["city"] == "Newport News"
    # An unset optional like member_id must not appear in the update.
    assert "member_id" not in dumped


def test_spoken_lines_mentions_core_fields_as_separate_sentences():
    # _spoken_lines feeds the paced, line-by-line TTS readback in ReviewStage.on_enter —
    # each fact must be its own sentence (not one comma-joined line) so the caller hears a
    # real pause after each one instead of a single run-on readback.
    lines = _spoken_lines(_full_state())
    joined = " ".join(lines)
    assert "Katherine Johnson" in joined
    assert "1918-08-26" in joined
    assert "Hampton, VA 23666" in joined
    assert all(line.endswith(".") for line in lines)
