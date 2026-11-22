"""Request/response contracts (they also drive the OpenAPI / Swagger document)."""
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

TERM_PATTERN = r"^\d{4}-(FALL|SPRING|SUMMER)$"
Currency = Literal["TRY", "USD", "EUR"]
# Money is stored as Decimal (exact) but rendered as a JSON number.
Money = Annotated[Decimal, PlainSerializer(lambda v: float(v), return_type=float, when_used="json")]


class CamelModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


# ---------- auth ----------
class LoginRequest(CamelModel):
    username: str = Field(min_length=1, max_length=60)
    password: str = Field(min_length=1, max_length=200)


class TokenResponse(CamelModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    role: str
    student_no: str | None = None


# ---------- query tuition ----------
class TermBalance(CamelModel):
    term: str
    tuition_total: Money = Field(serialization_alias="tuitionTotal")
    paid_amount: Money = Field(serialization_alias="paidAmount")
    balance: Money
    currency: str


class TuitionQueryResponse(CamelModel):
    student_no: str = Field(serialization_alias="studentNo")
    scope: Literal["TERM", "OVERALL"] = Field(
        description="TERM when ?term= was given, otherwise OVERALL (sum of all terms)"
    )
    term: str | None = None
    tuition_total: Money = Field(serialization_alias="tuitionTotal")
    paid_amount: Money = Field(serialization_alias="paidAmount")
    balance: Money
    currency: str = Field(description="Currency of the records, or MIXED if terms differ")
    terms: list[TermBalance]


# ---------- pay tuition ----------
class PaymentRequest(CamelModel):
    student_no: str = Field(alias="studentNo", min_length=1, max_length=20)
    term: str = Field(pattern=TERM_PATTERN, examples=["2026-FALL"])
    amount: Money = Field(gt=0, max_digits=12, decimal_places=2)
    transaction_id: str = Field(alias="transactionId", min_length=1, max_length=64)


class PaymentResponse(CamelModel):
    payment_status: Literal["SUCCESS"] = Field(serialization_alias="paymentStatus")
    transaction_id: str = Field(serialization_alias="transactionId")
    student_no: str = Field(serialization_alias="studentNo")
    term: str
    paid_amount: Money = Field(serialization_alias="paidAmount", description="Amount of this payment")
    total_paid: Money = Field(serialization_alias="totalPaid")
    remaining_balance: Money = Field(serialization_alias="remainingBalance")
    currency: str


# ---------- admin ----------
class AddTuitionRequest(CamelModel):
    student_no: str = Field(alias="studentNo", min_length=1, max_length=20)
    term: str = Field(pattern=TERM_PATTERN, examples=["2026-FALL"])
    amount: Money = Field(gt=0, max_digits=12, decimal_places=2)
    currency: Currency


class AddTuitionResponse(CamelModel):
    transaction_status: Literal["CREATED", "UPDATED"] = Field(serialization_alias="transactionStatus")
    student_no: str = Field(serialization_alias="studentNo")
    term: str
    amount: Money
    currency: str
    paid_amount: Money = Field(serialization_alias="paidAmount")
    balance: Money


class BatchRowError(CamelModel):
    line: int
    student_no: str | None = Field(default=None, serialization_alias="studentNo")
    error: str


class BatchResponse(CamelModel):
    transaction_status: Literal["COMPLETED", "PARTIAL", "FAILED"] = Field(
        serialization_alias="transactionStatus"
    )
    total_rows: int = Field(serialization_alias="totalRows")
    success_count: int = Field(serialization_alias="successCount")
    failure_count: int = Field(serialization_alias="failureCount")
    created: int
    updated: int
    errors: list[BatchRowError]


class UnpaidItem(CamelModel):
    student_no: str = Field(serialization_alias="studentNo")
    full_name: str = Field(serialization_alias="fullName")
    tuition_total: Money = Field(serialization_alias="tuitionTotal")
    paid_amount: Money = Field(serialization_alias="paidAmount")
    balance: Money
    currency: str


class UnpaidPage(CamelModel):
    term: str
    page: int
    page_size: int = Field(serialization_alias="pageSize")
    total_items: int = Field(serialization_alias="totalItems")
    total_pages: int = Field(serialization_alias="totalPages")
    items: list[UnpaidItem]
