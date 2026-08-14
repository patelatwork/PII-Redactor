"""HTTP service around the redaction pipeline.

    POST /redact    multipart file  -> redacted .docx download
    POST /analyze   {"text": "..."} -> detected entities, nothing rewritten
    POST /redact/text {"text":"..."} -> redacted string + mapping
    GET  /health                     -> liveness/readiness
    GET  /                           -> a one-page upload form

Design notes for whoever operates this:

* The spaCy model is loaded **once at startup**, not per request. It costs a few
  seconds and ~200 MB; doing it lazily would put that on the first user's
  request and make the readiness probe lie.
* Uploads are streamed to a temp directory and deleted in a ``finally`` block.
  Nothing is persisted: the service holds personal data only for the duration of
  one request.
* ``/redact`` does **not** return the re-identification mapping. Getting the
  redacted file and the key to undo it from the same endpoint defeats the point.
  Use the CLI (or ``/redact/text``, which is explicit about it) when the mapping
  is genuinely needed.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from piiredact import RedactionConfig, Redactor, __version__
from piiredact.documents import SUPPORTED_SUFFIXES, redact_document
from piiredact.types import PIIType

log = logging.getLogger("piiredact.service")

CONFIG = RedactionConfig.from_env()
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm the model and the compiled patterns before the readiness probe passes.
    started = time.perf_counter()
    Redactor(CONFIG).analyze("Warm up: Jane Doe, jane@example.com, +91 20 12345678.")
    log.info("pipeline warm in %.1fs (spacy=%s)", time.perf_counter() - started,
             CONFIG.spacy_model or "disabled")
    yield


app = FastAPI(
    title="PII Redaction Service",
    version=__version__,
    summary="Detect and pseudonymise personal data in documents.",
    lifespan=lifespan,
)


# --------------------------------------------------------------------------
# models
# --------------------------------------------------------------------------


class TextRequest(BaseModel):
    text: str = Field(..., max_length=2_000_000)
    seed: str | None = Field(None, description="override the surrogate seed")
    types: list[PIIType] | None = Field(None, description="restrict to these PII types")


class EntityOut(BaseModel):
    type: str
    start: int
    end: int
    text: str
    recognizer: str
    score: float


class AnalyzeResponse(BaseModel):
    entities: list[EntityOut]
    counts: dict[str, int]


class RedactTextResponse(BaseModel):
    text: str
    counts: dict[str, int]
    mapping: list[dict[str, str]]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _redactor(seed: str | None, types: list[PIIType] | None) -> Redactor:
    config = CONFIG
    if seed or types:
        config = CONFIG.with_(
            seed=seed or CONFIG.seed,
            enabled_types=tuple(types) if types else CONFIG.enabled_types,
        )
    return Redactor(config)


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "version": __version__,
        "spacy_model": CONFIG.spacy_model,
        "types": [t.value for t in CONFIG.enabled_types],
    }


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(request: TextRequest) -> AnalyzeResponse:
    redactor = _redactor(request.seed, request.types)
    entities = redactor.analyze(request.text)
    return AnalyzeResponse(
        entities=[
            EntityOut(
                type=e.type.value,
                start=e.start,
                end=e.end,
                text=e.text,
                recognizer=e.recognizer,
                score=round(e.score, 3),
            )
            for e in entities
        ],
        counts=redactor.summarise(entities),
    )


@app.post("/redact/text", response_model=RedactTextResponse)
def redact_text(request: TextRequest) -> RedactTextResponse:
    redactor = _redactor(request.seed, request.types)
    result = redactor.redact(request.text)
    return RedactTextResponse(
        text=result.text,
        counts=redactor.summarise(result.entities),
        mapping=redactor.mapping_rows(),
    )


@app.post("/redact")
async def redact_file(file: UploadFile = File(...)) -> FileResponse:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(
            status_code=415,
            detail=f"unsupported file type {suffix!r}; expected one of {SUPPORTED_SUFFIXES}",
        )

    workdir = Path(tempfile.mkdtemp(prefix="piiredact-"))
    source = workdir / f"input{suffix}"
    stem = Path(file.filename or "document").stem
    destination = workdir / f"{stem} - REDACTED.docx"

    try:
        written = 0
        with source.open("wb") as fh:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="file too large")
                fh.write(chunk)

        request_id = uuid.uuid4().hex[:8]
        started = time.perf_counter()
        redactor = Redactor(CONFIG)
        entities = redact_document(source, destination, redactor)
        # Log counts only -- never the values themselves.
        log.info(
            "req=%s bytes=%d entities=%d in %.1fs %s",
            request_id, written, len(entities),
            time.perf_counter() - started, redactor.summarise(entities),
        )

        return FileResponse(
            destination,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=destination.name,
            headers={"X-PII-Entities-Redacted": str(len(entities)), "X-Request-Id": request_id},
            background=_cleanup(workdir),
        )
    except HTTPException:
        shutil.rmtree(workdir, ignore_errors=True)
        raise
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        log.exception("redaction failed")
        raise HTTPException(status_code=500, detail="redaction failed") from None


def _cleanup(path: Path):
    """Delete the working directory after the response has been sent."""
    from starlette.background import BackgroundTask

    return BackgroundTask(shutil.rmtree, path, ignore_errors=True)
