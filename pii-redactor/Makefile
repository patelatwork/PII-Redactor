# Convenience targets. Everything here is a one-liner you could type by hand --
# the point is that nobody has to guess the incantation.
#
# On Windows without make, run the commands under each target directly, or use
# `pwsh -File run.ps1 <target>`.

PDF ?= data/input/Red Herring Prospectus.docx.pdf
OUT ?= output/Red Herring Prospectus - REDACTED.docx
PY  ?= python

.PHONY: help install install-ner test lint eval redact analyze serve docker docker-run clean

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install:  ## install the package plus dev and service extras
	$(PY) -m pip install -e ".[dev,service]"

install-ner: install  ## add the spaCy layer and its English model
	$(PY) -m pip install -e ".[ner]"
	$(PY) -m spacy download en_core_web_sm

test:  ## run the test suite
	$(PY) -m pytest

lint:  ## style and static checks
	$(PY) -m ruff check src service eval tests

eval:  ## rebuild the gold set and score the pipeline
	$(PY) eval/build_gold.py
	$(PY) eval/evaluate.py

redact:  ## redact the prospectus into output/
	$(PY) -m piiredact.cli redact "$(PDF)" -o "$(OUT)" --report-dir output

analyze:  ## detect only, print a summary
	$(PY) -m piiredact.cli analyze "$(PDF)"

serve:  ## run the HTTP service locally on :8000
	$(PY) -m uvicorn service.app:app --reload --port 8000

docker:  ## build the container image
	docker build -t piiredact .

docker-run:  ## run the container on :8000
	docker run --rm -p 8000:8000 -e PIIREDACT_SEED=$${PIIREDACT_SEED:-local-dev} piiredact

clean:  ## remove generated artefacts (including the reversible mapping)
	rm -rf output eval/results.json eval/gold/*_gold.json .pytest_cache .ruff_cache
