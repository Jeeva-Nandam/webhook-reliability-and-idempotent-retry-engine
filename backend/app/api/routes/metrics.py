from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.webhook import MetricsSummaryResponse
from app.services import webhook_service

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("/summary", response_model=MetricsSummaryResponse)
def metrics_summary(db: Session = Depends(get_db)):
    return MetricsSummaryResponse(**webhook_service.get_metrics_summary(db))
