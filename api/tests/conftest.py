"""Test fixtures. Tests run without cloud, gateway or LLM.

By default an isolated in-memory SQLite database is used so `pytest` works anywhere.
Set TEST_DATABASE_URL=postgresql+psycopg2://... to run the same suite against PostgreSQL.
(SQLite is ONLY used for unit tests; the application itself runs on PostgreSQL.)
"""
import os
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import Base, get_db  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Student, Tuition, User  # noqa: E402
from app.security import create_access_token, hash_password  # noqa: E402


def _make_engine():
    url = os.getenv("TEST_DATABASE_URL")
    if url:
        return create_engine(url)
    return create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)


@pytest.fixture()
def engine():
    eng = _make_engine()
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture()
def db_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def db(db_factory):
    s = db_factory()
    yield s
    s.close()


@pytest.fixture()
def seeded(db):
    db.add_all([
        Student(student_no="1001", full_name="Ayse Yilmaz"),
        Student(student_no="1002", full_name="Mehmet Demir"),
        Student(student_no="1003", full_name="Zeynep Kaya"),
    ])
    db.flush()
    db.add_all([
        User(username="ayse", password_hash=hash_password("pw"), role="student", student_no="1001"),
        User(username="admin", password_hash=hash_password("adminpw"), role="admin"),
        Tuition(student_no="1001", term="2026-FALL", amount=Decimal("45000"), currency="TRY", paid_amount=Decimal("15000")),
        Tuition(student_no="1001", term="2027-SPRING", amount=Decimal("45000"), currency="TRY", paid_amount=Decimal("0")),
        Tuition(student_no="1002", term="2026-FALL", amount=Decimal("45000"), currency="TRY", paid_amount=Decimal("45000")),
        Tuition(student_no="1003", term="2026-FALL", amount=Decimal("3000"), currency="USD", paid_amount=Decimal("500")),
    ])
    db.commit()
    return db


@pytest.fixture()
def client(db_factory, seeded):
    app = create_app(with_lifespan=False)

    def _get_db():
        s = db_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db
    with TestClient(app) as c:
        yield c


def bearer(username, role, student_no=None):
    token, _ = create_access_token(username, role, student_no)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def student_headers():
    return bearer("ayse", "student", "1001")


@pytest.fixture()
def admin_headers():
    return bearer("admin", "admin")
