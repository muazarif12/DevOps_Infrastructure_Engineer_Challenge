"""Persistence for patient records.

A single ``PatientStore`` object owns a SQLAlchemy session and exposes the handful of
operations the rest of the app needs. The database URL is read from the environment so the
same code runs against a local SQLite file or a managed database without edits; the SQLite
default resolves to an absolute path next to the package rather than the process directory.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import Date, DateTime, String, create_engine, func, select
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    sessionmaker,
)

from app.schema import NewPatient, PatientChanges

_DEFAULT_DB_FILE = Path(__file__).resolve().parent.parent / "records.db"
DATABASE_URL = os.environ.get("INTAKE_DB_URL", f"sqlite:///{_DEFAULT_DB_FILE}")

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
_engine = create_engine(
    DATABASE_URL,
    connect_args=_connect_args,
    # Recycle connections that a proxy/firewall/DB restart silently dropped instead of
    # surfacing "server closed the connection unexpectedly" mid-call. A no-op for SQLite.
    pool_pre_ping=True,
)
_new_session = sessionmaker(bind=_engine, expire_on_commit=False)


class ORMBase(DeclarativeBase):
    pass


class PatientRow(ORMBase):
    __tablename__ = "patient_records"

    record_id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    given_name: Mapped[str] = mapped_column(String(60), nullable=False)
    family_name: Mapped[str] = mapped_column(String(60), nullable=False)
    birth_date: Mapped[date] = mapped_column(Date, nullable=False)
    sex: Mapped[str] = mapped_column(String(20), nullable=False)
    phone: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    street: Mapped[str] = mapped_column(String(255), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str] = mapped_column(String(120), nullable=False)
    state: Mapped[str] = mapped_column(String(2), nullable=False)
    postal_code: Mapped[str] = mapped_column(String(10), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255))
    insurer: Mapped[str | None] = mapped_column(String(255))
    member_id: Mapped[str | None] = mapped_column(String(64))
    language: Mapped[str] = mapped_column(String(60), nullable=False, default="English")
    contact_name: Mapped[str | None] = mapped_column(String(255))
    contact_phone: Mapped[str | None] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


def create_schema() -> None:
    """Create tables if they do not yet exist. Safe to call on every startup."""
    ORMBase.metadata.create_all(_engine)


def as_public_dict(row: PatientRow) -> dict:
    """Serialize a row to a JSON-friendly dict for the API and the voice read-back."""
    return {
        "record_id": row.record_id,
        "given_name": row.given_name,
        "family_name": row.family_name,
        "birth_date": row.birth_date.isoformat(),
        "sex": row.sex,
        "phone": row.phone,
        "street": row.street,
        "unit": row.unit,
        "city": row.city,
        "state": row.state,
        "postal_code": row.postal_code,
        "email": row.email,
        "insurer": row.insurer,
        "member_id": row.member_id,
        "language": row.language,
        "contact_name": row.contact_name,
        "contact_phone": row.contact_phone,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "archived": row.archived_at is not None,
    }


class PatientStore:
    """Thin session wrapper. Prefer using it as a context manager."""

    def __init__(self, session: Session) -> None:
        self._session = session

    @classmethod
    def open(cls) -> PatientStore:
        return cls(_new_session())

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> PatientStore:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def ping(self) -> None:
        """Round-trip the database. Raises on failure — used by /readyz, which unlike the
        static /health endpoint is meant to actually fail when the DB is unreachable."""
        self._session.execute(select(1))

    # -- reads --------------------------------------------------------------

    def get(self, record_id: str) -> PatientRow | None:
        stmt = select(PatientRow).where(
            PatientRow.record_id == record_id,
            PatientRow.archived_at.is_(None),
        )
        return self._session.scalar(stmt)

    def find_by_phone(self, national_phone: str) -> PatientRow | None:
        stmt = (
            select(PatientRow)
            .where(PatientRow.phone == national_phone, PatientRow.archived_at.is_(None))
            .order_by(PatientRow.created_at.desc())
        )
        return self._session.scalar(stmt)

    def search(
        self,
        *,
        family_name: str | None = None,
        birth_date: date | None = None,
        national_phone: str | None = None,
    ) -> list[PatientRow]:
        stmt = select(PatientRow).where(PatientRow.archived_at.is_(None))
        if family_name:
            stmt = stmt.where(PatientRow.family_name == family_name)
        if birth_date:
            stmt = stmt.where(PatientRow.birth_date == birth_date)
        if national_phone:
            stmt = stmt.where(PatientRow.phone == national_phone)
        stmt = stmt.order_by(PatientRow.created_at.desc())
        return list(self._session.scalars(stmt).all())

    # -- writes -------------------------------------------------------------

    def add(self, data: NewPatient) -> PatientRow:
        row = PatientRow(**data.model_dump())
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row

    def apply_changes(self, row: PatientRow, changes: PatientChanges) -> PatientRow:
        # Only fields the caller actually supplied are touched — absent fields are left as-is.
        for field, value in changes.model_dump(exclude_unset=True).items():
            setattr(row, field, value)
        self._session.commit()
        self._session.refresh(row)
        return row

    def archive(self, row: PatientRow) -> PatientRow:
        row.archived_at = datetime.now(tz=timezone.utc)
        self._session.commit()
        self._session.refresh(row)
        return row
