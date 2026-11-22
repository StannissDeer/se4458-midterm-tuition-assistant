"""Business logic, independent of HTTP so it can be unit tested directly."""
import csv
import io
import math
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import settings
from .errors import ApiError
from .models import MobileQueryUsage, Payment, Student, Tuition
from .schemas import TERM_PATTERN

_TERM_RE = re.compile(TERM_PATTERN)
_CURRENCIES = {"TRY", "USD", "EUR"}
_CENT = Decimal("0.01")


# ---------------------------------------------------------------- query tuition
def query_tuition(db: Session, student_no: str, term: str | None) -> dict:
    if term is not None and not _TERM_RE.match(term):
        raise ApiError(400, "INVALID_TERM", "term must look like 2026-FALL, 2027-SPRING or 2027-SUMMER")
    if db.get(Student, student_no) is None:
        raise ApiError(404, "STUDENT_NOT_FOUND", f"Student {student_no} not found")

    stmt = select(Tuition).where(Tuition.student_no == student_no).order_by(Tuition.term)
    if term:
        stmt = stmt.where(Tuition.term == term)
    rows = db.scalars(stmt).all()
    if term and not rows:
        raise ApiError(404, "TUITION_NOT_FOUND", f"No tuition record for {student_no} in {term}")

    terms = [
        {
            "term": t.term,
            "tuition_total": t.amount,
            "paid_amount": t.paid_amount,
            "balance": t.balance,
            "currency": t.currency,
        }
        for t in rows
    ]
    currencies = {t.currency for t in rows}
    return {
        "student_no": student_no,
        "scope": "TERM" if term else "OVERALL",
        "term": term,
        "tuition_total": sum((t.amount for t in rows), Decimal("0")),
        "paid_amount": sum((t.paid_amount for t in rows), Decimal("0")),
        "balance": sum((t.balance for t in rows), Decimal("0")),
        "currency": currencies.pop() if len(currencies) == 1 else ("MIXED" if currencies else "TRY"),
        "terms": terms,
    }


def consume_mobile_quota(db: Session, student_no: str, today: date | None = None) -> int:
    """Atomically increments today's counter for the student and returns the remaining
    quota. Keyed by student number (identity), not client IP."""
    today = today or datetime.now(timezone.utc).date()
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:  # sqlite (unit tests only)
        from sqlalchemy.dialects.sqlite import insert

    stmt = (
        insert(MobileQueryUsage)
        .values(student_no=student_no, query_date=today, count=1)
        .on_conflict_do_update(
            index_elements=["student_no", "query_date"],
            set_={"count": MobileQueryUsage.count + 1},
        )
        .returning(MobileQueryUsage.count)
    )
    count = db.execute(stmt).scalar_one()
    db.commit()
    limit = settings.MOBILE_DAILY_LIMIT
    if count > limit:
        raise ApiError(
            429,
            "RATE_LIMIT_EXCEEDED",
            f"Mobile tuition query limit of {limit} per student per day exceeded",
            {"Retry-After": "86400", "X-RateLimit-Limit": str(limit), "X-RateLimit-Remaining": "0"},
        )
    return limit - count


# ---------------------------------------------------------------- pay tuition
def pay_tuition(db: Session, student_no: str, term: str, amount: Decimal, transaction_id: str) -> dict:
    amount = Decimal(amount).quantize(_CENT)
    if amount <= 0:
        raise ApiError(400, "INVALID_AMOUNT", "Payment amount must be greater than zero")

    if db.scalar(select(Payment.id).where(Payment.transaction_id == transaction_id)) is not None:
        raise ApiError(409, "DUPLICATE_TRANSACTION", f"Transaction {transaction_id} was already processed")

    tuition = db.scalar(
        select(Tuition)
        .where(Tuition.student_no == student_no, Tuition.term == term)
        .with_for_update()  # serialises concurrent payments on the same tuition row
    )
    if tuition is None:
        raise ApiError(404, "TUITION_NOT_FOUND", f"No tuition record for {student_no} in {term}")
    if tuition.balance <= 0:
        raise ApiError(409, "ALREADY_PAID", f"Tuition for {term} is already fully paid")
    if amount > tuition.balance:
        raise ApiError(
            422,
            "AMOUNT_EXCEEDS_BALANCE",
            f"Payment {amount} exceeds the remaining balance {tuition.balance} {tuition.currency}",
        )

    tuition.paid_amount = Decimal(tuition.paid_amount) + amount
    db.add(
        Payment(
            transaction_id=transaction_id,
            tuition_id=tuition.id,
            student_no=student_no,
            term=term,
            amount=amount,
            status="SUCCESS",
        )
    )
    try:
        db.commit()
    except IntegrityError:  # race on the unique transaction_id
        db.rollback()
        raise ApiError(409, "DUPLICATE_TRANSACTION", f"Transaction {transaction_id} was already processed")

    return {
        "payment_status": "SUCCESS",
        "transaction_id": transaction_id,
        "student_no": student_no,
        "term": term,
        "paid_amount": amount,
        "total_paid": tuition.paid_amount,
        "remaining_balance": tuition.balance,
        "currency": tuition.currency,
    }


# ---------------------------------------------------------------- add tuition
def upsert_tuition(db: Session, student_no: str, term: str, amount: Decimal, currency: str, commit=True) -> dict:
    """Duplicate-handling rule: (student_no, term) is unique. If a record already exists it is
    UPDATED with the new amount/currency, unless that would put it below what was already paid
    or change the currency after a payment - those are rejected with 409."""
    amount = Decimal(amount).quantize(_CENT)
    if amount <= 0:
        raise ApiError(400, "INVALID_AMOUNT", "Tuition amount must be greater than zero")
    if not _TERM_RE.match(term):
        raise ApiError(400, "INVALID_TERM", "term must look like 2026-FALL")
    if currency not in _CURRENCIES:
        raise ApiError(400, "INVALID_CURRENCY", f"currency must be one of {sorted(_CURRENCIES)}")

    if db.get(Student, student_no) is None:
        # Assumption: admin is the source of truth for enrolment, so unknown students are created.
        db.add(Student(student_no=student_no, full_name=""))
        db.flush()

    tuition = db.scalar(
        select(Tuition).where(Tuition.student_no == student_no, Tuition.term == term).with_for_update()
    )
    if tuition is None:
        tuition = Tuition(student_no=student_no, term=term, amount=amount, currency=currency,
                          paid_amount=Decimal("0"))
        db.add(tuition)
        status = "CREATED"
    else:
        paid = Decimal(tuition.paid_amount)
        if paid > 0 and currency != tuition.currency:
            raise ApiError(409, "CURRENCY_LOCKED", "Currency cannot change after a payment was received")
        if amount < paid:
            raise ApiError(409, "AMOUNT_BELOW_PAID", f"New amount {amount} is below already paid {paid}")
        tuition.amount = amount
        tuition.currency = currency
        status = "UPDATED"

    if commit:
        db.commit()
    else:
        db.flush()
    return {
        "transaction_status": status,
        "student_no": student_no,
        "term": term,
        "amount": tuition.amount,
        "currency": tuition.currency,
        "paid_amount": tuition.paid_amount,
        "balance": tuition.balance,
    }


def add_tuition_batch(db: Session, csv_bytes: bytes) -> dict:
    """CSV header: studentNo,term,amount,currency. Each row is applied in its own savepoint,
    so one bad row does not roll back the others."""
    try:
        text = csv_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ApiError(400, "INVALID_CSV", "CSV must be UTF-8 encoded")

    reader = csv.DictReader(io.StringIO(text))
    required = {"studentNo", "term", "amount", "currency"}
    header = {h.strip() for h in (reader.fieldnames or [])}
    if not required.issubset(header):
        raise ApiError(400, "INVALID_CSV", "CSV header must be: studentNo,term,amount,currency")

    total = created = updated = 0
    errors: list[dict] = []
    for line_no, raw in enumerate(reader, start=2):
        row = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
        if not any(row.values()):
            continue
        total += 1
        student_no = row.get("studentNo") or None
        try:
            if not student_no:
                raise ApiError(400, "INVALID_STUDENT", "studentNo is required")
            try:
                amount = Decimal(row.get("amount", ""))
            except InvalidOperation:
                raise ApiError(400, "INVALID_AMOUNT", f"amount '{row.get('amount')}' is not a number")
            with db.begin_nested():
                res = upsert_tuition(db, student_no, row.get("term", ""), amount,
                                     row.get("currency", "").upper(), commit=False)
            created += res["transaction_status"] == "CREATED"
            updated += res["transaction_status"] == "UPDATED"
        except ApiError as e:
            errors.append({"line": line_no, "student_no": student_no, "error": f"{e.code}: {e.message}"})
    db.commit()

    success = created + updated
    status = "COMPLETED" if not errors else ("FAILED" if success == 0 else "PARTIAL")
    return {
        "transaction_status": status,
        "total_rows": total,
        "success_count": success,
        "failure_count": len(errors),
        "created": created,
        "updated": updated,
        "errors": errors,
    }


# ---------------------------------------------------------------- unpaid status
def unpaid_tuition(db: Session, term: str, page: int, page_size: int) -> dict:
    if not _TERM_RE.match(term):
        raise ApiError(400, "INVALID_TERM", "term must look like 2026-FALL")
    if page < 1 or page_size < 1 or page_size > settings.MAX_PAGE_SIZE:
        raise ApiError(400, "INVALID_PAGING", f"page >= 1 and 1 <= pageSize <= {settings.MAX_PAGE_SIZE}")

    base = select(Tuition).where(Tuition.term == term, Tuition.paid_amount < Tuition.amount)
    total = db.scalar(select(func.count()).select_from(base.subquery()))
    rows = db.execute(
        base.add_columns(Student.full_name)
        .join(Student, Student.student_no == Tuition.student_no)
        .order_by(Tuition.student_no)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "term": term,
        "page": page,
        "page_size": page_size,
        "total_items": total,
        "total_pages": math.ceil(total / page_size) if total else 0,
        "items": [
            {
                "student_no": t.student_no,
                "full_name": name,
                "tuition_total": t.amount,
                "paid_amount": t.paid_amount,
                "balance": t.balance,
                "currency": t.currency,
            }
            for t, name in rows
        ],
    }
