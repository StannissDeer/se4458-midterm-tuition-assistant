"""Banking App - authenticated tuition query and simulated payment."""
from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from .. import services
from ..db import get_db
from ..schemas import PaymentRequest, PaymentResponse, TuitionQueryResponse
from ..security import Principal, ensure_student_access, get_current_principal

router = APIRouter(prefix="/banking", tags=["Banking App"])

_AUTH_ERRORS = {401: {"description": "Missing/invalid token"}, 403: {"description": "Not your record"}}


@router.get(
    "/tuition/{studentNo}",
    response_model=TuitionQueryResponse,
    summary="Query tuition (authenticated)",
    description="Same contract as the mobile query (term → scope=TERM, no term → scope=OVERALL) "
    "but requires a JWT whose student_no matches, or an admin token. No daily limit.",
    responses={**_AUTH_ERRORS, 404: {"description": "Not found"}},
)
def query_tuition_banking(
    student_no: str = Path(alias="studentNo", max_length=20),
    term: str | None = Query(default=None, examples=["2026-FALL"]),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    ensure_student_access(principal, student_no)
    return services.query_tuition(db, student_no, term)


@router.post(
    "/tuition/payments",
    response_model=PaymentResponse,
    status_code=201,
    summary="Pay tuition (simulated, partial or full)",
    description="Records a simulated payment. Rejects duplicate transaction IDs (409) and "
    "amounts above the remaining balance (422). Only the student themself may pay.",
    responses={**_AUTH_ERRORS, 404: {"description": "No tuition for term"},
               409: {"description": "Duplicate transaction / already paid"},
               422: {"description": "Amount exceeds balance or invalid input"}},
)
def pay_tuition(
    body: PaymentRequest,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    ensure_student_access(principal, body.student_no, allow_admin=False)
    return services.pay_tuition(db, body.student_no, body.term, body.amount, body.transaction_id)
