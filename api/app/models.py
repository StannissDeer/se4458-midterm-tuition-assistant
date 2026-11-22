"""Database model (see docs/ER_DIAGRAM.md)."""
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Student(Base):
    __tablename__ = "students"

    student_no: Mapped[str] = mapped_column(String(20), primary_key=True)
    full_name: Mapped[str] = mapped_column(String(120), default="")
    email: Mapped[str | None] = mapped_column(String(120), nullable=True)

    tuitions: Mapped[list["Tuition"]] = relationship(back_populates="student")


class User(Base):
    """Login identity. role = 'student' (bound to one student_no) or 'admin'."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20))
    student_no: Mapped[str | None] = mapped_column(
        ForeignKey("students.student_no"), nullable=True
    )

    __table_args__ = (CheckConstraint("role IN ('student','admin')", name="ck_user_role"),)


class Tuition(Base):
    """One tuition assessment per (student, term)."""

    __tablename__ = "tuitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_no: Mapped[str] = mapped_column(ForeignKey("students.student_no"), index=True)
    term: Mapped[str] = mapped_column(String(20), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3))
    paid_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    student: Mapped[Student] = relationship(back_populates="tuitions")
    payments: Mapped[list["Payment"]] = relationship(back_populates="tuition")

    __table_args__ = (
        UniqueConstraint("student_no", "term", name="uq_tuition_student_term"),
        CheckConstraint("amount > 0", name="ck_tuition_amount_positive"),
        CheckConstraint("paid_amount >= 0 AND paid_amount <= amount", name="ck_tuition_paid"),
    )

    @property
    def balance(self) -> Decimal:
        return Decimal(self.amount) - Decimal(self.paid_amount)


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    transaction_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    tuition_id: Mapped[int] = mapped_column(ForeignKey("tuitions.id"))
    student_no: Mapped[str] = mapped_column(String(20), index=True)
    term: Mapped[str] = mapped_column(String(20))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(20), default="SUCCESS")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    tuition: Mapped[Tuition] = relationship(back_populates="payments")


class MobileQueryUsage(Base):
    """Per-student daily counter for the University Mobile App query limit."""

    __tablename__ = "mobile_query_usage"

    student_no: Mapped[str] = mapped_column(String(20), primary_key=True)
    query_date: Mapped[date] = mapped_column(Date, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
