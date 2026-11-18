"""Optional local JSON API for automation and demo."""
import json

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from .core import audit_documents

app = FastAPI(title="Engineering Spec & SI-Unit Auditor", version="0.1.0")


class AuditRequest(BaseModel):
    documents: dict[str, str] = Field(min_length=1, max_length=12)
    tolerance: float = Field(default=0.01, ge=0.0, le=0.2)


@app.get("/health")
def health():
    return {"status": "ok", "mode": "local-deterministic"}


@app.post("/audit")
def audit(payload: AuditRequest):
    try:
        return audit_documents(payload.documents, payload.tolerance)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.exception_handler(RequestValidationError)
def validation_error(_request: Request, exc: RequestValidationError):
    """Standard 422 body. A rejected NaN/Infinity input cannot be echoed in standard JSON, so it is left out (never an HTTP 500)."""
    errors = jsonable_encoder(exc.errors())
    try:
        json.dumps(errors, allow_nan=False)
    except ValueError:
        errors = [{key: value for key, value in error.items() if key not in ("input", "ctx")} for error in errors]
    return JSONResponse(status_code=422, content={"detail": errors})
