from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import health, metrics, test_downstream, webhooks
from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()
configure_logging()

app = FastAPI(title=settings.APP_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(webhooks.router, prefix=settings.API_V1_PREFIX)
app.include_router(health.router, prefix=settings.API_V1_PREFIX)
app.include_router(metrics.router, prefix=settings.API_V1_PREFIX)
app.include_router(test_downstream.router, prefix=settings.API_V1_PREFIX)


@app.get("/")
def root():
    return {"service": settings.APP_NAME, "docs": "/docs"}
