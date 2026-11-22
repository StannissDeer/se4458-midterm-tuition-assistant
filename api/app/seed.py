"""Creates tables and loads demo data. Run: python -m app.seed  (from the api/ folder)."""
from decimal import Decimal

from sqlalchemy import select

from .db import Base, SessionLocal, engine
from .models import Student, Tuition, User
from .security import hash_password

STUDENTS = [
    # student_no, name, username, password
    ("2021001", "Ayse Yilmaz", "ayse", "ayse123"),
    ("2021002", "Mehmet Demir", "mehmet", "mehmet123"),
    ("2021003", "Zeynep Kaya", "zeynep", "zeynep123"),
    ("2021004", "Can Ozturk", "can", "can123"),
]

TUITIONS = [
    # student_no, term, amount, currency, paid   (USD, matching the $ amounts in the policy)
    ("2021001", "2026-FALL", "3000.00", "USD", "1000.00"),   # balance 2000 -> installment threshold met
    ("2021001", "2027-SPRING", "3000.00", "USD", "0.00"),
    ("2021002", "2026-FALL", "3000.00", "USD", "3000.00"),   # fully paid
    ("2021003", "2026-FALL", "3000.00", "USD", "2800.00"),   # balance 200 -> below the $300 threshold
    ("2021004", "2026-FALL", "2250.00", "USD", "0.00"),      # 25% merit-reduced tuition, unpaid
]


def seed() -> None:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        for no, name, username, pw in STUDENTS:
            if db.get(Student, no) is None:
                db.add(Student(student_no=no, full_name=name, email=f"{username}@student.example"))
        db.flush()
        for no, _, username, pw in STUDENTS:
            if db.scalar(select(User).where(User.username == username)) is None:
                db.add(User(username=username, password_hash=hash_password(pw), role="student", student_no=no))
        if db.scalar(select(User).where(User.username == "admin")) is None:
            db.add(User(username="admin", password_hash=hash_password("admin123"), role="admin"))
        for no, term, amount, cur, paid in TUITIONS:
            exists = db.scalar(select(Tuition).where(Tuition.student_no == no, Tuition.term == term))
            if exists is None:
                db.add(Tuition(student_no=no, term=term, amount=Decimal(amount), currency=cur,
                               paid_amount=Decimal(paid)))
        db.commit()
    print("Seed complete.")


if __name__ == "__main__":
    seed()
