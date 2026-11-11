"""Optional local JSON API for automation and demo."""
from fastapi import FastAPI, HTTPException
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
