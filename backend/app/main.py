from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .catalog import UNIT_BY_ID, public_catalog
from .inference import Ensemble, Formation, ModelLoadError, SideState, Simulator


class FormationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80)
    unit_id: int
    lane: int
    level: int = Field(default=1, ge=1, le=9)


class SideInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    formations: list[FormationInput] = Field(default_factory=list)
    unlocked_unit_ids: list[int] = Field(default_factory=list)
    tech_investment: dict[int, int] = Field(default_factory=dict)

    @field_validator("tech_investment")
    @classmethod
    def validate_tech(cls, value: dict[int, int]) -> dict[int, int]:
        if any(amount < 0 or amount % 100 for amount in value.values()):
            raise ValueError("tech_investment must be non-negative multiples of 100")
        return value


class EvaluateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    side_a: SideInput
    side_b: SideInput
    temperature: float = Field(default=5.0, ge=1.0, le=20.0)


app = FastAPI(title="Mechabellum Winrate Simulator", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    locations = [".".join(str(part) for part in error.get("loc", ())) for error in exc.errors()]
    if any("tech_investment" in location for location in locations):
        code = "invalid_tech_investment"
    elif any("temperature" in location for location in locations):
        code = "invalid_temperature"
    else:
        code = "invalid_request"
    return JSONResponse(status_code=422, content={"detail": {"code": code, "message": "request validation failed", "fields": locations}})


try:
    SIMULATOR = Simulator()
    MODEL_ERROR: str | None = None
except ModelLoadError as exc:
    SIMULATOR = None
    MODEL_ERROR = str(exc)


def _to_side(data: SideInput) -> SideState:
    ids = [formation.id for formation in data.formations]
    if len(ids) != len(set(ids)):
        raise HTTPException(status_code=422, detail={"code": "duplicate_formation_id", "message": "formation ids must be unique"})
    if any(formation.unit_id not in UNIT_BY_ID for formation in data.formations):
        raise HTTPException(status_code=422, detail={"code": "invalid_unit_id", "message": "unit is not available in the first-front shop"})
    if any(formation.lane not in {-2, -1, 0, 1, 2} for formation in data.formations):
        raise HTTPException(status_code=422, detail={"code": "invalid_lane", "message": "lane must be one of -2,-1,0,1,2"})
    if any(unit_id not in UNIT_BY_ID for unit_id in data.unlocked_unit_ids) or any(unit_id not in UNIT_BY_ID for unit_id in data.tech_investment):
        raise HTTPException(status_code=422, detail={"code": "invalid_unit_id", "message": "unit is not available in the first-front shop"})
    return SideState(
        formations=[Formation(formation.id, formation.unit_id, formation.lane, formation.level) for formation in data.formations],
        unlocked_unit_ids=set(data.unlocked_unit_ids),
        tech_investment=dict(data.tech_investment),
    )


@app.get("/api/health")
def health() -> dict[str, object]:
    if SIMULATOR is None:
        raise HTTPException(status_code=503, detail={"code": "model_unavailable", "message": MODEL_ERROR})
    return {
        "status": "ok",
        "model": "full-6-7",
        "folds": len(SIMULATOR.ensemble.models),
        "feature_dim": 6235,
        "warning": SIMULATOR.ensemble.version_warning,
    }


@app.get("/api/catalog")
def catalog() -> dict[str, object]:
    return {"units": public_catalog(), "tiers": [0, 50, 200, 350]}


@app.post("/api/evaluate")
def evaluate(payload: EvaluateInput) -> dict[str, object]:
    if SIMULATOR is None:
        raise HTTPException(status_code=503, detail={"code": "model_unavailable", "message": MODEL_ERROR})
    side_a, side_b = _to_side(payload.side_a), _to_side(payload.side_b)
    if not side_a.formations or not side_b.formations:
        raise HTTPException(status_code=422, detail={"code": "empty_board_side", "message": "both sides need at least one formation"})
    try:
        result = SIMULATOR.evaluate(side_a, side_b, payload.temperature)
    except ValueError as exc:
        if str(exc) == "empty_board_side":
            raise HTTPException(status_code=422, detail={"code": "empty_board_side", "message": "both sides need at least one formation"}) from exc
        raise
    return {
        "probability": {"side_a": result.side_a, "side_b": result.side_b, "fold_std": result.fold_std},
        "recommendations": result.recommendations,
        "model": {"name": "full-6-7", "folds": 3, "feature_dim": 6235, "temperature": payload.temperature},
    }


FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
