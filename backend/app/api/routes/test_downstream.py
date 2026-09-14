"""
Test-only route exposing the simulated downstream service directly, so you
can manually verify the simulator's behavior without going through the full
webhook pipeline.
"""

from fastapi import APIRouter, HTTPException

from app.schemas.webhook import DownstreamSimulationRequest
from app.services.downstream_service import (
    DownstreamServerError,
    DownstreamTimeoutError,
    call_downstream,
)

router = APIRouter(prefix="/test", tags=["test"])


@router.post("/process")
def simulate_process(body: DownstreamSimulationRequest):
    try:
        code = call_downstream(mode=body.mode)
        return {"result": "success", "status_code": code}
    except DownstreamServerError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except DownstreamTimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc))
