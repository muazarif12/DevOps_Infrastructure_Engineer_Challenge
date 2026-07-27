"""Validation-layer tests: normalizers and the record models."""

import datetime as dt

import pytest
from pydantic import ValidationError

from app.schema import (
    NewPatient,
    PatientChanges,
    canonical_sex,
    canonical_state,
    canonical_zip,
    parse_birth_date,
    reject_future_date,
    tidy_name,
    to_national_phone,
)

VALID = dict(
    given_name="Ada",
    family_name="Lovelace",
    birth_date="1990-05-01",
    sex="female",
    phone="(212) 736-5000",
    street="350 5th Ave",
    city="New York",
    state="ny",
    postal_code="10118",
)


def test_phone_accepts_varied_formats_and_normalizes():
    for form in ("212-736-5000", "(212) 736-5000", "+1 212 736 5000", "2127365000"):
        assert to_national_phone(form) == "2127365000"


def test_phone_rejects_short_and_garbage():
    for bad in ("123", "not a phone", "555"):
        with pytest.raises(ValueError):
            to_national_phone(bad)


def test_birth_date_parses_multiple_formats():
    expected = dt.date(1990, 5, 1)
    for form in ("1990-05-01", "05/01/1990", "May 1 1990", "May 1, 1990"):
        assert parse_birth_date(form) == expected


def test_future_birth_date_rejected():
    tomorrow = dt.date.today() + dt.timedelta(days=1)
    with pytest.raises(ValueError):
        reject_future_date(tomorrow)


def test_state_normalizes_and_validates():
    assert canonical_state("ca") == "CA"
    with pytest.raises(ValueError):
        canonical_state("ZZ")


def test_zip_shapes():
    assert canonical_zip("10118") == "10118"
    assert canonical_zip("10118-0110") == "10118-0110"
    with pytest.raises(ValueError):
        canonical_zip("1011")


def test_sex_synonyms_map_to_canonical():
    assert canonical_sex("m") == "Male"
    assert canonical_sex("WOMAN") == "Female"
    assert canonical_sex("prefer not to say") == "Decline to answer"
    with pytest.raises(ValueError):
        canonical_sex("unknown-token")


def test_name_rejects_digits():
    with pytest.raises(ValueError):
        tidy_name("Ada2")


def test_new_patient_happy_path_normalizes_fields():
    record = NewPatient(**VALID)
    assert record.phone == "2127365000"
    assert record.state == "NY"
    assert record.sex == "Female"
    assert record.birth_date == dt.date(1990, 5, 1)
    assert record.language == "English"


def test_new_patient_requires_core_fields():
    incomplete = dict(VALID)
    del incomplete["phone"]
    with pytest.raises(ValidationError):
        NewPatient(**incomplete)


def test_patient_changes_tracks_only_supplied_fields():
    changes = PatientChanges(city="Brooklyn")
    dumped = changes.model_dump(exclude_unset=True)
    assert dumped == {"city": "Brooklyn"}
