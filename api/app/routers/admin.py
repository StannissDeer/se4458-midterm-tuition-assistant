"""University Web Site - Admin operations (admin JWT required)."""
from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.orm import Session

from .. import services
from ..config import settings
from ..db import get_db
from ..schemas import AddTuitionRequest, AddTuitionResponse, BatchResponse, UnpaidPage
from ..security import Principal, require_admin

router = APIRouter(prefix="/admin", tags=["University Web Site - Admin"])

_AUTH_ERRORS = {401: {"description": "Missing/invalid token"}, 403: {"description": "Admin role required"}}


@router.post(
    "/tuition",
    response_model=AddTuitionResponse,
    summary="Add or update tuition for a student and term",
    description="Duplicate handling: (studentNo, term) is unique. An existing record is UPDATED; "
    "the update is rejected (409) if the new amount is below the amount already paid, or if the "
    "currency changes after a payment.",
    responses={**_AUTH_ERRORS, 409: {"description": "Conflicts with existing payments"}},
)
def add_tuition(
    body: AddTuitionRequest,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return services.upsert_tuition(db, body.student_no, body.term, body.amount, body.currency)


@router.post(
    "/tuition/batch",
    response_model=BatchResponse,
    summary="Add tuition in batch from CSV",
    description="Upload a CSV with header `studentNo,term,amount,currency`. Each row follows the "
    "single Add Tuition rules; valid rows are saved even if others fail. Returns success/failure counts.",
    responses={**_AUTH_ERRORS, 400: {"description": "Malformed CSV"}},
)
async def add_tuition_batch(
    file: UploadFile = File(..., description="CSV file"),
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return services.add_tuition_batch(db, await file.read())


@router.get(
    "/tuition/unpaid",
    response_model=UnpaidPage,
    summary="List students with unpaid tuition for a term (paged)",
    responses={**_AUTH_ERRORS, 400: {"description": "Invalid term or paging"}},
)
def unpaid_tuition_status(
    term: str = Query(examples=["2026-FALL"]),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=settings.MAX_PAGE_SIZE, alias="pageSize"),
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return services.unpaid_tuition(db, term, page, page_size)
