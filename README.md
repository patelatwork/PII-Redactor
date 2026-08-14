# PII Redaction Tool

Reads a document and writes a copy in which every piece of personal data has
been replaced by a **realistic, consistent fake alternative** — not a black box.
If `Rashi Patil` becomes `John Doe`, then `rashi.patil@gmail.com` becomes
`john.doe@example.com` and `Rashi` alone, twelve pages later, still becomes
`John` — everywhere, every run.

Built for the assignment against the attached **Red Herring Prospectus**
(4,639 paragraphs); the redacted deliverable is
[`output/Red Herring Prospectus - REDACTED.docx`](output/).

```
666 entities replaced across 4,639 paragraphs in 21s
  PERSON        264   ADDRESS  51
  ORGANIZATION  228   PHONE    36
  EMAIL          52   URL      27   DIN  8
```

| | Precision | Recall | Token accuracy |
|---|---:|---:|---:|
| **Held-out sample** (23 entities, drawn *after* development) | **0.955** | **0.913** | 0.979 |
| Development sample (66 entities, used while building) | 1.000 | 1.000 | 1.000 |
| Synthetic suite (26 entities, types absent from the filing) | 1.000 | 0.962 | 0.985 |

**Read the held-out row as the honest estimate.** The development sample was used
iteratively to find and fix defects, so scoring 1.000 on it means the known
defects are fixed — not that the tool is perfect. The held-out sample was drawn
at random after the detectors were finished and scored once; its three failures
are diagnosed and fixed in EVALUATION.md §3.

Full methodology, per-type tables, ablations and error analysis:
**[EVALUATION.md](EVALUATION.md)**.

---

## Quick start

```bash
pip install -e ".[ner,service]"
python -m spacy download en_core_web_sm        # optional; rules-only works too

# redact a document
piiredact redact "data/input/Red Herring Prospectus.docx" \
                 -o "output/Red Herring Prospectus - REDACTED.docx" \
                 --report-dir output

# see what it would redact, without writing a document
piiredact analyze "data/input/Red Herring Prospectus.docx"
```

Or over HTTP:

```bash
docker compose up --build          # http://localhost:8000
curl -F 'file=@ticket.docx' http://localhost:8000/redact -o redacted.docx
```

`.docx`, `.pdf`, `.txt` and `.md` go in; a `.docx` comes out (`.txt` out is
supported too). Alongside the document you get `summary.json`, `entities.csv`
(every detection with offsets, recognizer and score — the audit trail) and
`mapping.json` (the reversible `original → surrogate` map; disable it with
`--no-mapping`).

---

## Approach

A **layered pipeline**, because no single technique is good at all nine PII
types. Each layer is deliberately narrow and the resolver picks between them.

```
document ──► segments ──► [ 1. deterministic patterns ]
                          [ 2. structural rules       ] ──► resolve overlaps ──►
                          [ 3. statistical NER        ]         │
                                    ▲                           │
                          [ 4. document-wide propagation ] ◄────┘
                                                                │
                          surrogate generation ◄────────────────┘
                                    │
                          run-preserving rewrite ──► redacted .docx
```

**1. Deterministic patterns** (`recognizers/patterns.py`) — e-mail, phone, SSN,
credit card, IP, date of birth, PAN, Aadhaar, DIN, passport, URL. Every one is
either structurally unambiguous or backed by a check: **Luhn** for cards,
**Verhoeff** for Aadhaar, SSA range rules for SSNs, an explicit label for DIN and
passport, birth-context words for dates. Regex alone would be a precision
disaster in a document made of numbers; the checksums are what make it safe.

**2. Structural rules** (`organizations.py`, `addresses.py`, `persons.py`) —
these read the *shape* of a corporate filing:

- **Companies** are found by anchoring on a legal suffix (`… Limited`, `… LLP`,
  `… Family Trust`) and walking left across the title-case run. On this corpus
  that beats model NER outright.
- **Addresses** are found by anchoring on the postal tail — a 6-digit PIN
  followed by a state and/or "India", or a US `City, ST 12345` — and expanding
  left to the nearest boundary. Word stores each line of an address block as its
  own paragraph, so two extra rules cover the fragments: a PIN that closes a
  paragraph after a spaced dash, and a premises line ("Gat No. 11/3, …, Village
  Birdewadi") that has no postal code at all.
- **People** are found by the positions filings use: after an honorific, before
  or after a designation (`Managing Director`, `Company Secretary`), after a
  `Contact Person:` label, inside an `OUR PROMOTERS:` list.

**3. spaCy NER** (`ner.py`) — the recall net for names no rule anticipated.
Every candidate is filtered through the same plausibility predicate the rules
use, and scored *below* deterministic hits so the resolver prefers patterns on
overlap. **Optional**: with spaCy absent the pipeline logs once and runs
rules-only (`--no-ner`). spaCy's `ORG` label is **off by default** — measured, it
cost far more precision than it bought (EVALUATION.md §6).

**4. Document-wide propagation** (`propagation.py`) — *the layer that matters
most.* A person is introduced once in a high-signal context and then referred to
sixty times in prose with no local cue. Detecting the first mention is a
precision problem; the rest is string matching. So pass 1 collects only
*confident* detections, and pass 2 sweeps the entire document for those names
plus their predictable short forms (`Kushal Subbayya Hegde` → `Kushal Hegde`;
`Nuvama Wealth Management Limited` → `Nuvama`), and for `<unknown first name> +
<confirmed surname>`. It supplies 223 of the 666 detections; disabling it costs
21% of them, concentrated exactly where risk is highest.

**Overlap resolution** (`resolution.py`) — recognizers legitimately collide (an
address contains a city that NER calls an organisation). Ranked by type priority
→ span length → score → position, then greedy non-overlapping selection.
Deterministic identifiers beat inferred names; container types beat what they
contain.

**Surrogates** (`surrogates.py`) — three properties, by construction:

- **Consistent.** Person identities are resolved to a canonical form, so short
  mentions render from the same fake persona as the full name, and e-mail local
  parts follow the person they name.
- **Deterministic.** Every value derives from `HMAC(seed, type|value)`. Re-runs
  are byte-identical; two shards processed on different machines agree. Rotating
  the seed re-randomises everything.
- **Safe.** Generated values come from reserved ranges wherever one exists —
  `example.com` (RFC 2606), `192.0.2.0/24` etc. (RFC 5737), `2001:db8::/32`
  (RFC 3849), SSNs in the never-issued 900 block, Luhn-valid cards that are not
  real. The tool can never accidentally mint a live identifier.

Formats are preserved. Real output from the run:

```
Cherag Gyara                     -> Vikram Parker
cherag.gyara@icicibank.com       -> vikram.parker@lighthouseenterprises.example.com
Kushal Subbayya Hegde            -> Meera Mohan Iyer
Kushal Hegde                     -> John Doe
Sarthak Malvadkar                -> Rohan Rao
cs.connect@kshinternational.com  -> cs.connect@vertextechnologies.example.com
+ 91 20 45053237                 -> + 91 70 18086749
www.kshinternational.com         -> www.larkspurindustries.example.com
KSH International Limited        -> Crestline Traders Limited
Kirtane & Pandit LLP             -> Amberfield Materials LLP
```

The first two lines are the point: a person and *their* e-mail address, detected
by different recognizers in different paragraphs, resolve to the same fake
identity. The country code and spacing survive on the phone, the role mailbox
`cs.connect` survives while its domain does not, and the legal suffixes
`Limited`/`LLP` are kept so the sentences still read. (`Kushal Hegde` gets its
own persona rather than `Meera Iyer` because four different promoters share
those two name tokens — the tool refuses to guess which one is meant, and says
so in EVALUATION.md.)

**Document IO** (`documents/`) — `.docx` is rewritten **in place at the run
level**, so bold, sizes, styles, tables, headers and footers all survive; runs
inside hyperlinks are included, since that is where Word hides e-mail addresses.
Paragraphs are de-duplicated by XML element identity: a merged table cell is
returned once per grid position it spans, and this filing yields 5,715 visits
from 4,639 distinct paragraphs — applying one paragraph's replacements twice
would rewrite it against offsets the first pass already invalidated.

PDFs are extracted block-wise with PyMuPDF, repaired (the cover-page table comes
back as `['Email:\ncs.connect@acme.co', 'm Telephone: + 91 20', '45053237']`) and
materialised as a `.docx` that then goes through the *same* rewriter — one
substitution implementation, not two.

---

## Why these tools

| Chose | Over | Because |
|---|---|---|
| Custom recognizers | **Microsoft Presidio** | Presidio is the obvious off-the-shelf answer and covers the generic types well. It has no notion of *consistent* surrogates across an entity's short and long forms, which is the assignment's headline requirement, and its Indian-context recognizers would still have needed writing. Building the analyzer directly kept the surrogate/identity logic and the extension point in one place. |
| spaCy `en_core_web_sm` | transformer NER | 30× faster, no GPU, and on this corpus the rules do the work anyway (EVALUATION.md §6). The model is a config string — swapping to `en_core_web_trf` is one line. |
| python-docx run-level rewrite | replace `paragraph.text` | Replacing paragraph text collapses every run and destroys all formatting. A prospectus that comes back unstyled is not a usable deliverable. |
| PyMuPDF | pdfplumber / pypdf | Markedly cleaner text on this file — pypdf returned double-spaced, mid-word-broken output that no regex could recover from. |

---

## Precision: what we deliberately do *not* redact

The assignment asks us to be explicit. Each of these is a decision, applied
consistently in both the tool and the gold labels, and each is switchable.

| Left alone | Reasoning | Override |
|---|---|---|
| Regulators and exchanges (SEBI, BSE, NSE, RBI, RoC, NSDL) and their `*.gov.in` domains | Public bodies, not private parties. Redacting them destroys a regulatory filing's meaning and protects nobody. | `--redact-public-institutions` |
| CIN, SEBI/firm registration numbers | Identify a filing entity, not a person. | `extra_deny` |
| Order / ticket / invoice / PO numbers | Called out in the assignment. A `400-25-1000` invoice reference is structurally a valid SSN, so SSN detection is vetoed by nearby `invoice`/`order`/`ticket`/`reference` cues. | `extra_deny` |
| Role mailbox local parts (`ipo@`, `customercare@`, `cs.connect@`) | Identify a desk, not a human. **The domain is still replaced**, so the organisation does not leak. | — |
| Bare city / state names ("Pune", "Maharashtra") | A city is not a mailing address. The full addresses containing them *are* redacted. | `extra_deny` |
| Unlabelled 10-digit numbers | In a filing those are share counts and rupee amounts. Phones need a `+` country code or a `Telephone:`/`Mobile:` label. | — |

One deliberately *inclusive* call: **company websites are redacted**, because a
URL identifies a company as precisely as its name, and doing one without the
other would be theatre.

---

## Known false positives and false negatives

Measured, not guessed — every item below comes from the evaluation run.

**On the held-out sample the tool made one false positive and two misses out of
23 entities**, and all three were real defects rather than labelling disputes:
`Institute of Chartered Accountants` was allowlisted only in its longer "…of
India" spelling; `Cherag Gyara` was lost because the greedy name pattern
swallowed the following field's words and then rejected the whole match; and
`+ 91 (20) 6729 5100` was missed because the phone pattern disallowed
parentheses. All three are fixed. EVALUATION.md §3 records the before-and-after
in full, because a fixed defect found on a held-out sample means that sample is
spent.

**False positives across the current build: none**, on any of the three suites,
at either matching mode — over 1,605 tokens of which 1,167 carry no PII.

**False negatives, in full:**

- **Initials plus surname** — `DM Shetty`, `SA Shetty`. The known-surname rule
  needs a capitalised *word* before the surname; accepting `[A-Z]{2}` would match
  table headers.
- **A middle initial between first name and surname** — `Narayana B. Shetty`
  breaks the two-token adjacency the surname rule relies on.
- **`Ananya Deshpande`** in free prose (synthetic suite) — the small spaCy model
  misses it. The clearest case for upgrading the NER layer.
- **A company whose name carries no legal suffix** — `Trilegal` is never written
  as "Trilegal LLP", so nothing seeds the single-token short form.
- **A company name split across three table cells** — `KSH Distriparks Private
  Limited`; `KSH` is redacted by propagation, the remainder spans paragraph
  boundaries that recognizers deliberately cannot cross.
- **A factory unit's name** (`Chakan Unit No. 2 (Birdewadi)`) — no street
  locator, no PIN, so not treated as an address. Arguably correct.

The general shape: the tool is very safe against over-redaction, and its residual
risk is a rare, singly-mentioned name in an unstructured position.

---

## Adding a new PII type

Five small edits, no changes to the pipeline:

1. **`types.py`** — add the member to `PIIType` and give it a rank in
   `TYPE_PRIORITY` (higher wins on overlap; deterministic ids high, inferred
   names low, container types above what they contain).
2. **A recognizer** — subclass `RegexRecognizer` (set `pattern`, `pii_type`,
   `score`; override `validate()` for a checksum or context test and `skip()`
   for a policy exemption), or write any object with `name`, `types` and
   `analyze(text, config)`.
3. **`recognizers/__init__.py`** — append it to `default_registry()`.
4. **`surrogates.py`** — add a `_your_type` method and register it in
   `_HANDLERS`. Use `self._stream(...)` for determinism and prefer a reserved
   range if the identifier has one.
5. **Tests** — a positive case, a near-miss negative, and a surrogate
   format/validity check.

Worked example — a UK National Insurance number:

```python
# recognizers/patterns.py
class NationalInsuranceRecognizer(RegexRecognizer):
    """UK NINO: two prefix letters (excluding D, F, I, Q, U, V), six digits,
    and a suffix letter A-D."""

    name = "nino"
    pii_type = PIIType.NINO
    types = (PIIType.NINO,)
    score = 0.92
    pattern = re.compile(
        r"\b(?!BG|GB|NK|KN|TN|NT|ZZ)[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z]"
        r"\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b"
    )
```

```python
# surrogates.py
def _nino(self, value: str) -> str:
    stream = self._stream(PIIType.NINO, value)
    return _apply_digit_mask(value, stream.digits(6))   # keeps the letters and spacing
```

Both the propagation pass and the document adapters pick it up with no further
work. `test_every_type_has_a_surrogate_handler` fails loudly if you add the enum
member and forget step 4.

---

## Deployment

Designed to be run by someone who is not you, in a place you cannot log into.

**Container.** Multi-stage build; a virtualenv is assembled in the builder and
copied into a slim runtime, so pip, caches and wheels never ship. Runs as an
unprivileged user (uid 10001) because the process handles other people's
personal data. `HEALTHCHECK` hits `/health`.

```bash
docker build -t piiredact .                                      # with the spaCy layer
docker build --build-arg INSTALL_NER=false -t piiredact:slim .   # rules only, no model
docker compose up
```

The compose file runs the container **read-only** with `/tmp` on tmpfs, all
capabilities dropped and `no-new-privileges` — so uploaded documents never touch
a real disk.

**Service.** `service/app.py`, FastAPI:

| Route | Purpose |
|---|---|
| `POST /redact` | multipart upload → redacted `.docx` download |
| `POST /redact/text` | JSON in → redacted text **+ mapping** |
| `POST /analyze` | JSON in → detected entities, nothing rewritten |
| `GET /health` | liveness / readiness |
| `GET /` | a one-page upload form for humans |
| `GET /docs` | generated OpenAPI docs |

Operational decisions worth knowing:

- The spaCy model loads **once at startup**, not per request — otherwise the
  readiness probe lies and the first user pays 3 seconds.
- Uploads stream to a temp dir and are deleted in a `finally`. Nothing is
  persisted; personal data lives only for the duration of one request.
- `/redact` **does not** return the re-identification mapping. Shipping the
  redacted file and the key to undo it from the same endpoint defeats the point.
- Logs record entity **counts**, never values.
- One worker per container — each holds its own copy of the model, so scale with
  replicas, not `--workers`.

**Configuration** is env vars (`PIIREDACT_SEED`, `PIIREDACT_TYPES`,
`PIIREDACT_SPACY_MODEL`, `PIIREDACT_NER_ORGS`, …) or a YAML file
(`config.example.yaml`, `--config`). **Rotate `PIIREDACT_SEED` per tenant** —
the seed decides which fake values are generated, so two tenants sharing one
would receive the same surrogate for the same real name.

**CI** (`.github/workflows/ci.yml`) lints, runs the tests on Python 3.10 and
3.12 (3.10 *without* the NER extra, so the rules-only path stays exercised),
builds the image and smoke-tests the running container end to end. The
evaluation job runs only where the source document is present, and uploads
`results.json` as an artefact.

**Handling the mapping file.** `mapping.json` reverses the entire redaction and
is exactly as sensitive as the original document. Keep it if you need to
re-identify later (store it beside the original, not the output); pass
`--no-mapping` for one-way anonymisation. `.gitignore` and `.dockerignore`
exclude `output/` and the gold files so real personal data cannot be committed
or baked into an image by accident.

---

## Layout

```
src/piiredact/
  types.py          PIIType, Entity, priorities            (the whole data model)
  config.py         RedactionConfig, allowlists, env/YAML loading
  lexicons.py       word lists that drive the rules — tuning is data, not code
  analyzer.py       two-pass detection pipeline
  resolution.py     overlap resolution
  surrogates.py     deterministic, consistent fake-value generation
  redactor.py       the one object callers need
  recognizers/      base · patterns · organizations · addresses · persons · ner · propagation
  documents/        segments · docx_io · pdf_io
  cli.py            argparse CLI (no framework dependency)
service/            FastAPI app + a single-page upload UI
eval/               build_gold.py · evaluate.py · gold/ (hand-written labels)
tests/              92 tests: detection, precision, surrogates, docx IO, HTTP
output/             the redacted deliverable + audit artefacts
```

```bash
make install-ner    # install with the spaCy layer
make test           # 92 tests
make lint           # ruff
make eval           # rebuild gold, score, write eval/results.json
make redact         # redact the prospectus
make serve          # run the API locally
```

---

## Limitations

Stated plainly so the numbers are not read as stronger than they are:

- **The held-out estimate rests on 23 entities**, so the interval around
  0.955 / 0.913 is wide. 115 gold entities across all three suites; per-type
  figures for rarer types rest on 1–3 instances.
- **The held-out sample has been spent** — its failures were fixed, so current
  scores on it are development scores. A fresh draw would be needed to
  re-estimate.
- Single annotator, no inter-annotator agreement. Address boundaries are a
  judgement call, which is why both strict and partial matching are reported.
- One document, one domain, one locale — an Indian corporate filing plus English
  ticket-log text. Nothing is claimed about medical records, non-Latin scripts,
  or other address formats.
- PDF input is a **text** conversion: reading-order content is preserved,
  original page layout, table structure and images are not. `.docx` input keeps
  its formatting; `.docx` in / `.docx` out is the production path.
- Text inside embedded images is not read; OCR is out of scope.
- Runtime is ~21 s for the 4,639-paragraph filing single-threaded; `--no-ner`
  does the same document in ~15 s and, on this corpus, scores identically
  (EVALUATION.md §6).
