"""University Mobile App - no authentication, limited to N queries per student per day."""
from fastapi import APIRouter, Depends, Path, Query, Response
from sqlalchemy.orm import Session

from .. import services
from ..config import settings
from ..db import get_db
from ..schemas import TuitionQueryResponse

router = APIRouter(prefix="/mobile", tags=["University Mobile App"])


@router.get(
    "/tuition/{studentNo}",
    response_model=TuitionQueryResponse,
    summary="Query tuition (public, 3 calls per student per day)",
    description="Returns tuition total and balance. With `term` the result is for that term "
    "(scope=TERM); without it the overall balance across all terms is returned (scope=OVERALL). "
    "The daily limit is counted per **student number**, not per client IP.",
    responses={429: {"description": "Daily limit exceeded"}, 404: {"description": "Not found"}},
)
def query_tuition_mobile(
    response: Response,
    student_no: str = Path(alias="studentNo", max_length=20),
    term: str | None = Query(default=None, examples=["2026-FALL"]),
    db: Session = Depends(get_db),
):
    remaining = services.consume_mobile_quota(db, student_no)
    response.headers["X-RateLimit-Limit"] = str(settings.MOBILE_DAILY_LIMIT)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    return services.query_tuition(db, student_no, term)
