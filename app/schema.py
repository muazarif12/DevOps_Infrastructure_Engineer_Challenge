"""Validation and normalization for patient demographics.

The approach here is composition rather than inheritance: each field carries its own
reusable ``Annotated`` type (with the normalizer baked in), and the record models are
assembled from those types. That keeps the create and patch shapes small and avoids a
tall class hierarchy.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated

import phonenumbers
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    TypeAdapter,
    ValidationError,
)

from app.us_states import STATE_CODES

# --- Accepted vocabularies -------------------------------------------------

SEX_OPTIONS: tuple[str, ...] = ("Male", "Female", "Other", "Decline to answer")

# Spoken variants a caller might use, mapped onto the canonical option.
_SEX_SYNONYMS = {
    "m": "Male",
    "male": "Male",
    "man": "Male",
    "f": "Female",
    "female": "Female",
    "woman": "Female",
    "other": "Other",
    "nonbinary": "Other",
    "non-binary": "Other",
    "x": "Other",
    "decline": "Decline to answer",
    "declined": "Decline to answer",
    "decline to answer": "Decline to answer",
    "prefer not to say": "Decline to answer",
    "prefer not to answer": "Decline to answer",
    "no answer": "Decline to answer",
}

_NAME_ALLOWED = re.compile(r"^[A-Za-z][A-Za-z .'\-]*$")
_ZIP_SHAPE = re.compile(r"^\d{5}(?:-\d{4})?$")
_BIRTH_FORMATS = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%m-%d-%Y",
    "%B %d %Y",
    "%B %d, %Y",
    "%b %d %Y",
    "%b %d, %Y",
)


# --- Normalizers -----------------------------------------------------------


def tidy_name(raw: str) -> str:
    text = re.sub(r"\s+", " ", (raw or "").strip())
    if not text:
        raise ValueError("name cannot be empty")
    if len(text) > 60:
        raise ValueError("name is too long")
    if not _NAME_ALLOWED.fullmatch(text):
        raise ValueError("name may only contain letters, spaces, hyphens, apostrophes, or periods")
    return text


def to_national_phone(raw: str) -> str:
    """Parse any US-dialable form into its 10-digit national number."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("phone number cannot be empty")
    try:
        parsed = phonenumbers.parse(text, "US")
    except phonenumbers.NumberParseException:
        raise ValueError("phone number could not be understood") from None
    if not phonenumbers.is_valid_number(parsed):
        raise ValueError("that is not a valid US phone number")
    return phonenumbers.national_significant_number(parsed)


def parse_birth_date(raw: object) -> date:
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, str):
        candidate = raw.strip()
        for fmt in _BIRTH_FORMATS:
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
        raise ValueError("date of birth must look like 1990-05-01, 05/01/1990, or May 1 1990")
    raise ValueError("date of birth is not a recognizable date")


def reject_future_date(value: date) -> date:
    if value > date.today():
        raise ValueError("date of birth cannot be in the future")
    return value


def canonical_state(raw: str) -> str:
    code = (raw or "").strip().upper()
    if code not in STATE_CODES:
        raise ValueError("state must be a valid two-letter US code, e.g. CA")
    return code


def canonical_zip(raw: str) -> str:
    text = (raw or "").strip()
    if not _ZIP_SHAPE.fullmatch(text):
        raise ValueError("postal code must be 12345 or 12345-6789")
    return text


_EmailAdapter = TypeAdapter(EmailStr)


def canonical_email(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        raise ValueError("email cannot be empty")
    try:
        return _EmailAdapter.validate_python(text)
    except ValidationError:
        raise ValueError("that doesn't look like a valid email address") from None


def canonical_sex(raw: str) -> str:
    key = re.sub(r"\s+", " ", (raw or "").strip().lower())
    if key in _SEX_SYNONYMS:
        return _SEX_SYNONYMS[key]
    # Allow the caller to say the canonical value directly, any casing.
    for option in SEX_OPTIONS:
        if key == option.lower():
            return option
    raise ValueError(f"sex must be one of: {', '.join(SEX_OPTIONS)}")


# --- Reusable field types --------------------------------------------------

PersonName = Annotated[str, AfterValidator(tidy_name)]
NationalPhone = Annotated[str, BeforeValidator(to_national_phone)]
BirthDate = Annotated[date, BeforeValidator(parse_birth_date), AfterValidator(reject_future_date)]
StateCode = Annotated[str, BeforeValidator(canonical_state)]
PostalCode = Annotated[str, AfterValidator(canonical_zip)]
SexValue = Annotated[str, BeforeValidator(canonical_sex)]
ShortText = Annotated[str, AfterValidator(lambda s: s.strip())]


# --- Record models ---------------------------------------------------------


class NewPatient(BaseModel):
    """Everything required to register a patient. Used by the API POST and the save tool."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    given_name: PersonName
    family_name: PersonName
    birth_date: BirthDate
    sex: SexValue
    phone: NationalPhone
    street: str
    unit: str | None = None
    city: str
    state: StateCode
    postal_code: PostalCode

    email: EmailStr | None = None
    insurer: str | None = None
    member_id: str | None = None
    language: str = "English"
    contact_name: str | None = None
    contact_phone: NationalPhone | None = None


class PatientChanges(BaseModel):
    """A partial update. Only the fields actually supplied are applied downstream."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    given_name: PersonName | None = None
    family_name: PersonName | None = None
    birth_date: BirthDate | None = None
    sex: SexValue | None = None
    phone: NationalPhone | None = None
    street: str | None = None
    unit: str | None = None
    city: str | None = None
    state: StateCode | None = None
    postal_code: PostalCode | None = None
    email: EmailStr | None = None
    insurer: str | None = None
    member_id: str | None = None
    language: str | None = None
    contact_name: str | None = None
    contact_phone: NationalPhone | None = None
