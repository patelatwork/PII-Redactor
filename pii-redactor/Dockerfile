# Multi-stage build.
#
# Stage 1 installs the package and downloads the spaCy model into a virtualenv.
# Stage 2 copies only that virtualenv into a slim runtime, so build tooling,
# pip caches and the wheel archives never reach the shipped image.
#
#   docker build -t piiredact .
#   docker run --rm -p 8000:8000 piiredact
#
# Build without the statistical layer (~500 MB smaller, rules only):
#   docker build --build-arg INSTALL_NER=false -t piiredact:slim .

# ---------------------------------------------------------------- builder ---
FROM python:3.12-slim AS builder

ARG INSTALL_NER=true
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

RUN python -m venv "$VIRTUAL_ENV"

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --upgrade pip \
 && pip install ".[service]" \
 && if [ "$INSTALL_NER" = "true" ]; then \
        pip install ".[ner]" && python -m spacy download en_core_web_sm; \
    fi

# ---------------------------------------------------------------- runtime ---
FROM python:3.12-slim AS runtime

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIIREDACT_SEED=change-me-in-production

# Run as a non-root user: this process handles other people's personal data.
RUN useradd --create-home --uid 10001 redactor

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY service ./service

USER redactor
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status==200 else 1)"

# One worker per container; scale with replicas. Each worker holds its own copy
# of the spaCy model, so several workers in one container mostly waste memory.
CMD ["uvicorn", "service.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
