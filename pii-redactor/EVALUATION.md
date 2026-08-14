# Evaluation report

Run date: 2026-08-14 · commit: initial submission · seed: `piiredact-v1`
Reproduce with:

```bash
python eval/build_gold.py      # resolve hand-written labels to character offsets
python eval/evaluate.py        # score and write eval/results.json
```

---

## 1. What was evaluated, and why this way

The deliverable is a redacted document, but "the document looks clean" is not a
measurement. So the evaluation is built on a **hand-labelled gold set** and
scored per PII type.

Two things about the source corpus shaped the design:

1. **The prospectus does not contain four of the nine required PII types.** It is
   an Indian IPO filing: it is full of names, companies, addresses, e-mails and
   phone numbers, and contains **no** Social Security Numbers, credit cards, IP
   addresses or dates of birth. Reporting "recall = n/a" for four of the nine
   types would say nothing about whether they work.
2. **Exhaustively labelling 126 pages by hand is not feasible**, and a gold set
   generated from the tool's own output would only measure self-consistency.

So there are two suites:

| Suite | What it is | What it measures |
|---|---|---|
| **`prospectus_gold`** | 26 hand-labelled blocks sampled from the real filing, 80 entities | Real-world behaviour on names, companies, addresses, contacts, DINs — including precision on prose that contains no PII |
| **`synthetic_gold`** | 4 hand-written ticket-log records, 26 entities | The types the filing lacks — SSN, credit card, IP, date of birth — plus deliberate decoys |

### Sampling method (prospectus suite)

Blocks were chosen by **stratum**, not at random, because PII is extremely
unevenly distributed in a prospectus — roughly 85% of it sits in three sections.
Random sampling of 26 blocks out of 1,881 would have returned almost entirely
empty prose and measured nothing.

| Stratum | Records | Content |
|---|---|---|
| `cover` | 4 | Cover page: issuer, compliance officer, contact block, promoter list |
| `general_information` | 4 | Registered/corporate office, RoC address, compliance officer details |
| `board` | 9 | Director table rows: name, designation, DIN, home address |
| `intermediaries` | 4 | Lead managers and registrar: company, address, phone, e-mails, website, contact persons |
| `negative` | 5 | Running prose with **zero** PII, deliberately chosen because it is dense with capitalised jargon ("Anchor Investor Application Form", "Registrar of Companies", "Bidder's DP ID", the literal words "PAN" and "Aadhaar") |

The `negative` stratum is the precision test. Five of 26 records — 19% — are
there purely to catch over-redaction.

### Labelling procedure

`eval/gold/prospectus_labels.json` contains only **strings** written by hand
while reading the source text. `eval/build_gold.py` then pulls each block out of
the PDF, locates each labelled string, and resolves it to character offsets. If
a label does not occur verbatim, the build **fails loudly**. This means a
transcription error is impossible and the labels are auditable against the
source, while the tool's output was never consulted to create them.

### Metrics

Three views, because a single number hides the interesting failure:

- **Strict entity matching** — type *and* exact character span must match.
- **Partial entity matching** — type matches and spans overlap, paired
  one-to-one, best overlap first. This is the operationally meaningful view: if
  the tool replaced two extra words at the start of an address, the personal
  data still got redacted.
- **Token-level accuracy** — every whitespace token is classified
  redacted/not-redacted by both gold and system. Unlike entity matching this has
  true negatives, so **accuracy** has a well-defined denominator: all tokens in
  the sample.

The prospectus suite is scored **in full-document context** — the analyzer sees
all 126 pages and the gold blocks are sliced back out afterwards. That is how
the tool runs in production, and it matters: the propagation pass earns most of
its recall from mentions elsewhere in the file. (`--isolated` scores the sample
alone if you want to see the difference.)

---

## 2. Results

### 2.1 Prospectus suite — 26 records, 80 gold entities, 79 predicted

**Partial span matching (headline)**

| Type | TP | FP | FN | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|
| ADDRESS | 16 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| DIN | 8 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| EMAIL | 11 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| ORGANIZATION | 13 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| PERSON | 20 | 0 | 1 | 1.000 | 0.952 | 0.976 |
| PHONE | 6 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| URL | 5 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| **Micro-average** | **79** | **0** | **1** | **1.000** | **0.988** | **0.994** |

**Strict span matching**

| Type | TP | FP | FN | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|
| ADDRESS | 11 | 5 | 5 | 0.688 | 0.688 | 0.688 |
| DIN | 8 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| EMAIL | 11 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| ORGANIZATION | 13 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| PERSON | 20 | 0 | 1 | 1.000 | 0.952 | 0.976 |
| PHONE | 6 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| URL | 5 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| **Micro-average** | **74** | **5** | **6** | **0.937** | **0.925** | **0.931** |

**Token level** — 876 tokens: TP 380, FP 0, FN 43, TN 453

| Accuracy | Precision | Recall | F1 |
|---:|---:|---:|---:|
| **0.951** | **1.000** | 0.898 | 0.947 |

The entire strict/partial gap is ADDRESS: 5 spans where the tool redacted a
slightly wider block than the label (typically pulling in the preceding field
label or building name). No address was missed and none was invented. Token
recall (0.898) is lower than entity recall (0.988) for the same reason in
reverse — the one missed name plus the *narrower* parts of correctly-detected
addresses account for the 43 token-level misses.

**Token precision is 1.000: across 876 tokens, including 453 that carry no PII
at all, the tool redacted nothing it should not have.**

### 2.2 Synthetic suite — 4 records, 26 gold entities, 25 predicted

**Partial span matching**

| Type | TP | FP | FN | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|
| AADHAAR | 1 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| ADDRESS | 2 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| CREDIT_CARD | 3 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| DATE_OF_BIRTH | 3 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| DIN | 1 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| EMAIL | 2 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| IP_ADDRESS | 3 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| ORGANIZATION | 1 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| PAN | 1 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| PASSPORT | 1 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| PERSON | 2 | 0 | 1 | 1.000 | 0.667 | 0.800 |
| PHONE | 3 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| SSN | 2 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| **Micro-average** | **25** | **0** | **1** | **1.000** | **0.962** | **0.980** |

**Token level** — 264 tokens: TP 58, FP 2, FN 2, TN 202

| Accuracy | Precision | Recall | F1 |
|---:|---:|---:|---:|
| **0.985** | 0.967 | 0.967 | 0.967 |

The decoy record (`ticket-1004-decoys`) is worth calling out. It contains a
16-digit order number, a `555-12-3456789` ticket id, a `400-25-1000` invoice
reference, dotted version strings `6.011.111.111` and `3.47.2.1`, formatted
currency amounts, and the literal words "PAN" and "Aadhaar" — and **zero
entities were predicted in it**. The precision rules that make this work are:

- SSN rejects a 3-2-4 number when `invoice`/`order`/`ticket`/`reference`/`PO`
  appears just before it, unless an explicit `SSN` label overrides;
- credit cards must pass Luhn *and* carry a real brand prefix;
- IPv4 requires a private/documentation range or two octets above 31, and is
  vetoed outright by a nearby `version`/`build`/`schema` cue;
- Aadhaar requires a valid Verhoeff check digit;
- DIN and passport require an explicit label or an adjacent director designation.

### 2.3 Whole-document run

| | |
|---|---|
| Input | `Red Herring Prospectus.docx.pdf`, 126 pages, ~414k characters, 1,881 blocks |
| Output | `output/Red Herring Prospectus - REDACTED.docx` |
| Runtime | ~9 s end-to-end on one CPU core (extraction + NER + detection + rewrite) |
| Entities replaced | **583** |

| Type | Count | Distinct values |
|---|---:|---:|
| ORGANIZATION | 233 | 82 |
| PERSON | 186 | 44 |
| EMAIL | 52 | 26 |
| ADDRESS | 42 | 39 |
| PHONE | 35 | 21 |
| URL | 27 | 14 |
| DIN | 8 | 8 |

(ADDRESS has almost as many distinct values as occurrences because each address
is written slightly differently at each mention — different line wrapping,
different abbreviations — which is also why the surrogate for an address is
keyed on its exact text rather than on a resolved identity.)

**Independent leak check.** After redaction, the output `.docx` was re-scanned
for every original identifier. Occurrences remaining:

| Term | Before | After |
|---|---:|---:|
| `kshinternational` (issuer domain) | 5 | **0** |
| `KSH` (issuer acronym) | 8 | **0** |
| `Hegde` (promoter surname) | many | **0** |
| `Malvadkar`, `Nuvama`, `Trilegal`, `Kirtane`, `Waterloo`, `ICICI`, `HDFC`, `Distriparks` | many | **0** |
| `410 501`, `411 045` (office PIN codes) | several | **0** |
| `Shetty` | many | 2 |
| `Birdewadi` | 9 | 2 |
| `Pune` (bare city name) | 17 | 17 — *by design, see §4* |

---

## 3. Ablations — did each layer earn its place?

Same gold sets, same metrics, one component changed. Figures are micro-averaged
partial matching.

| Configuration | Prospectus P / R | Synthetic P / R |
|---|---|---|
| **Default** (rules + propagation + spaCy PERSON) | **1.000 / 0.988** | **1.000 / 0.962** |
| `--no-ner` (rules only, no spaCy at all) | 1.000 / 0.988 | 1.000 / 0.885 |
| `--ner-orgs` (also trust spaCy's ORG label) | 0.819 / 0.963 | 0.962 / 0.962 |

Three conclusions:

**The statistical layer contributes nothing on this corpus.** On the prospectus
sample, rules-only scores identically to rules+NER. Corporate filings introduce
people in structured positions — after an honorific, before a designation, after
a `Contact Person:` label — and rules read those positions exactly. spaCy earns
its keep on free prose: on the synthetic ticket log it lifts recall from 0.885
to 0.962 by finding names with no structural cue. It is kept on by default for
that reason, and `--no-ner` is a supported mode that removes a ~500 MB
dependency for structured-document workloads.

**Trusting spaCy's ORG label is actively harmful here** — precision falls from
1.000 to 0.819 while recall *also* drops slightly (wrong-shaped ORG spans win
overlap resolution against correct ones). On the first full run it produced
detections like "ASBA Bidder", "Basis of Allotment", "Anchor Investors" and
"Analysis of Financial Condition", which were then propagated document-wide. The
legal-suffix rule ("… Limited", "… LLP", "… Family Trust") plus short-form
propagation covers real company names without any of that, so
`ner_organizations` defaults to **off**. It remains a config flag, not a
deletion, because a corpus of unstructured prose would flip the trade-off.

**Propagation is the main recall mechanism.** Disabling it on the full document
drops PERSON from 186 to 153 and ORGANIZATION from 233 to 205 — a 10% loss
overall, concentrated exactly where privacy risk is highest: the short forms
("Rajesh Hegde", "KSH", "Nuvama") that appear in running prose far from any
introducing context.

---

## 4. Error analysis

### Every remaining false negative

| Type | Value | Why |
|---|---|---|
| PERSON | `Dinesh Hirachand \nMunot` | The block contains *only* this name, split across a line. No honorific, no designation, no other mention in the document to propagate from, and spaCy does not tag it. |
| PERSON | `Ananya Deshpande` (synthetic) | "KYC review for Ananya Deshpande, employed at …" — no structural cue, and spaCy's small model misses it. The medium/large model or a transformer would catch this; it is the clearest case for upgrading the NER layer. |
| PERSON | `DM Shetty`, `SA Shetty` (whole-document scan) | Initials-plus-surname. The known-surname rule requires a capitalised *word* before the surname; `[A-Z]{2}` initials are not accepted, because doing so would match table headers. |
| ADDRESS | `Chakan Unit No. 2 (Birdewadi)` (whole-document scan) | A factory unit's name, not a postal address — no PIN code, no state. Arguably not PII; left alone. |

### Every remaining false positive

None, in either suite, under partial matching. Under strict matching the only
errors are 5 ADDRESS spans that are **wider** than the gold label — the tool
redacted the address plus one adjacent field label. Over-redacting a field
label is not a privacy failure and does not damage the document's meaning;
it is counted as an error here because strict matching is the harsher lens.

### The classes of mistake seen during development

These were all found and fixed by running the evaluation; they are listed
because they characterise where this kind of tool goes wrong:

1. **Statistical NER on domain text.** `en_core_web_sm` tagged Indian locality
   and society names as `PERSON` ("Deccan Gymkhana", "Buena Monte", "Gram
   Jyoti") and financial jargon as `ORG`. Fixed with a shape predicate (all
   tokens Title Case *or* all ALL CAPS, never mixed), a domain stop-token list,
   and disabling the ORG label.
2. **Greedy right-to-left span growth.** The organisation pattern walks left
   from a legal suffix and crossed into the previous company name
   ("HDFC Bank Limited and ICICI Bank Limited" as one entity). Fixed by
   splitting candidates at interior legal suffixes and emitting each part.
3. **Address left boundaries.** With no reliable left edge, address spans
   swallowed the phone number and company name above them. Fixed with an
   explicit boundary set (paragraph break, sentence end, field label, legal
   suffix, e-mail, phone, id column) — still the weakest part of the pipeline
   and the sole source of strict-match error.
4. **Word-boundary lookarounds excluding `.`.** Name matching used
   `(?![\w.@])` to avoid matching inside e-mail addresses; this silently
   rejected every name that ended a sentence. Fixed with `(?!\w)(?![.@]\w)`.
   This one cost real recall and was invisible without per-entity scoring.
5. **PDF extraction fragmenting values.** The cover-page table came back as
   `['Email:\ncs.connect@acme.co', 'm Telephone: + 91 20', '45053237']`, so the
   e-mail and the phone number were never seen as units. Fixed with a
   continuation-merge pass in the PDF adapter.
6. **Case-sensitive suffix matching.** `FAMILY TRUST` in the ALL-CAPS promoter
   list did not match the `Family Trust` suffix; all six promoter trusts were
   missed. Fixed by listing upper-case suffix spellings explicitly rather than
   using `IGNORECASE`, which would have broken the capitalisation signal the
   name pattern depends on.

---

## 5. Explicit precision choices

The assignment asks us to be explicit about what we chose *not* to treat as PII.
Each of these is a deliberate call, is applied consistently in the gold labels
and the tool, and is switchable:

| Not redacted | Reasoning | Override |
|---|---|---|
| Regulator / exchange names (SEBI, BSE, NSE, RBI, RoC, NSDL, CDSL) | Public bodies, not private parties. Redacting them destroys a regulatory filing's meaning without protecting anyone. | `--redact-public-institutions` |
| Government hostnames (`sebi.gov.in`, `bseindia.com`, `*.gov.in`) | Same reasoning, applied to URLs and e-mail domains. | same flag |
| Corporate identifiers: CIN, registration number, SEBI registration number, firm registration number | Identify a *filing entity*, not a person. | `extra_deny` in config |
| Order / ticket / invoice / PO numbers | Explicitly called out in the assignment as things to leave alone. | `extra_deny` |
| Role mailboxes (`ipo@`, `customercare@`, `cs.connect@`) — local part only | Identifies a desk, not a human. The **domain is still replaced**, so the organisation is not leaked. | n/a |
| Bare city and state names ("Pune", "Maharashtra") | A city is not a mailing address. Redacting every "Pune" would remove ~17 occurrences of ordinary geography and make the document unreadable, for no privacy gain — the full addresses containing them *are* redacted. | `extra_deny` |
| Unlabelled 10-digit numbers | In a filing these are share counts and rupee amounts. Phone numbers are matched only with a `+` country code or an explicit `Telephone:`/`Mobile:` label. | see `PhoneRecognizer` |

The one deliberately *inclusive* choice: **company websites are redacted**
(`www.kshinternational.com` → `www.larkspurindustries.example.com`), because a
company URL identifies the company exactly as its name does, and redacting the
name while leaving the URL would be pointless.

---

## 6. Limitations of this evaluation

Stated plainly, so the numbers are not read as stronger than they are:

- **80 + 26 = 106 gold entities is a small sample.** Per-type figures for the
  rarer types rest on 1–3 instances each; a 1.000 there means "the handful we
  tested all worked", not a tight confidence interval.
- **The sample is stratified, not random**, so it over-represents PII-dense
  sections. It is designed to test both recall (dense strata) and precision
  (negative stratum), but the micro-averages are not an unbiased estimate of
  document-wide performance.
- **Single annotator, single pass.** There is no inter-annotator agreement
  figure. Address boundaries in particular are a judgement call, which is
  exactly why both strict and partial matching are reported.
- **The synthetic suite was written by the same person who wrote the rules**,
  so it demonstrates the detectors work on well-formed inputs and resist the
  specific decoys chosen — not that they generalise to arbitrary ticket logs.
- **One document, one domain, one locale.** Everything here is measured on an
  Indian corporate filing plus English ticket-log text. Nothing is claimed about
  medical records, non-Latin scripts, or other address formats.

The honest summary: on the sampled material the tool detects essentially all
PII with no false positives, its weakest dimension is address span boundaries,
and its single recurring failure mode is a person named exactly once with no
structural cue anywhere in the document.
