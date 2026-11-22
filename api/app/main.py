"""Tuition REST API. All operations are versioned under /api/v1 (a v2 router can be mounted
side by side without breaking v1 clients)."""
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from .db import Base, engine
from .errors import register_error_handlers
from .routers import admin, auth, banking, mobile


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


def build_v1() -> APIRouter:
    v1 = APIRouter(prefix="/api/v1")
    for r in (auth.router, mobile.router, banking.router, admin.router):
        v1.include_router(r)
    return v1


def create_app(with_lifespan: bool = True) -> FastAPI:
    app = FastAPI(
        title="University Tuition API",
        version="1.0.0",
        description="Tuition query, payment and administration services used by the University "
        "Mobile App, partner Banking Apps, the Admin web site and the AI Tuition Assistant. "
        "In production every route is reached through the API Gateway.",
        lifespan=lifespan if with_lifespan else None,
    )
    register_error_handlers(app)
    app.include_router(build_v1())

    @app.get("/health", tags=["Health"])
    def health():
        return {"status": "ok"}

    return app


app = create_app()
