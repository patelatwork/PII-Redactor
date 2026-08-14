# Evaluation report

Run date: 2026-08-14 · seed: `piiredact-v1` · input: `Red Herring Prospectus.docx`
Reproduce with:

```bash
python eval/build_gold.py      # resolve hand-written labels to character offsets
python eval/evaluate.py        # score and write eval/results.json
```

---

## 1. Headline

| Suite | What it is | Precision | Recall | F1 | Token accuracy |
|---|---|---:|---:|---:|---:|
| **Held-out** (23 entities, 40 paragraphs) | Randomly drawn *after* development finished | **0.955** | **0.913** | 0.933 | 0.979 |
| Development (66 entities, 56 paragraphs) | Stratified sample, used iteratively while building | 1.000 | 1.000 | 1.000 | 1.000 |
| Synthetic (26 entities, 4 records) | The PII types the filing does not contain | 1.000 | 0.962 | 0.980 | 0.985 |

**Read the held-out row as the honest estimate.** The development sample was
used to find and fix defects, which makes it a development set: scoring 1.000 on
it says the known defects are fixed, not that the tool is perfect. §3 explains
this in full, including what happened after the held-out failures were repaired.

Whole-document run: **666 entities replaced across 4,639 paragraphs in 21 s**,
with zero occurrences of any promoter name, director name, company name, office
address, phone number or e-mail domain surviving in the output (§5).

---

## 2. Method

The deliverable is a redacted document, but "it looks clean" is not a
measurement. So the evaluation rests on **hand-labelled gold sets**, scored per
PII type. Two properties of the corpus shaped the design.

**The prospectus contains only five of the nine required PII types.** It is an
Indian IPO filing: full of names, companies, addresses, phone numbers and
e-mails, and containing **no** Social Security Numbers, credit cards, IP
addresses or dates of birth. Reporting "n/a" for four of nine types would say
nothing about whether they work, hence the synthetic suite.

**Exhaustively labelling 4,639 paragraphs by hand is not feasible**, and a gold
set generated from the tool's own output would only measure self-consistency.
Hence sampling — and hence the need for a held-out sample to keep the sampling
honest.

### 2.1 The three suites

| Suite | Records | Entities | Purpose |
|---|---:|---:|---|
| `prospectus_gold` | 56 | 66 | Development. Stratified over the PII-dense sections plus deliberate negatives |
| `holdout_gold` | 40 | 23 | **Held out.** Randomly drawn after the detectors were finished |
| `synthetic_gold` | 4 | 26 | SSN, credit card, IP, date of birth — plus decoys |

### 2.2 Sampling

**Development sample — stratified, deliberately.** PII is very unevenly
distributed in a prospectus: roughly 85% of it sits in three sections. Random
sampling of 56 paragraphs out of 4,639 would have returned almost entirely empty
prose and measured nothing.

| Stratum | Records | Content |
|---|---:|---|
| `cover` | 8 | Cover page: issuer, compliance officer, contact block, promoter list |
| `general_information` | 14 | Registered/corporate office, RoC address, compliance officer details |
| `board` | 18 | Director table: name, designation, DIN, home address |
| `intermediaries` | 12 | Lead managers and registrar: company, address, phone, e-mails, website, contacts |
| `negative` | 12 | Paragraphs with **zero** PII, chosen because they are dense with capitalised jargon ("Anchor Investor Application Form", "Registrar of Companies", "Depository Participant's Identification Number", designations, CIN and SEBI registration numbers) |

Twelve of 56 records — 21% — exist purely to catch over-redaction.

**Held-out sample — random, reproducible, untouched.** Drawn *after* the
detectors were complete, with the seeds recorded in the labels file:

- `random` (30 records): `random.Random(20260814).sample()` over every paragraph
  of 25–400 characters outside the regions inspected during development. This
  draw is ~87% negatives, which is what the document actually looks like, so it
  measures precision well and recall thinly.
- `contact` (10 records): `random.Random(7).sample()` over the same pool
  filtered to paragraphs containing `@`, `Telephone`, `Contact Person` or a
  PIN-and-state pattern — supplying the recall signal.

Neither draw was cherry-picked; the seeds and pools are stated in
`eval/gold/holdout_labels.json` and the selection is reproducible.

### 2.3 Labelling

`eval/gold/*_labels.json` contains only **strings**, written by hand while
reading the source. `eval/build_gold.py` then opens the `.docx`, checks each
record's `anchor` against the paragraph at its `index`, and resolves every label
to character offsets. A label that does not occur verbatim **fails the build**.
So transcription errors are impossible, the labels are auditable against the
source, and the tool's output was never consulted to produce them.

Index *and* anchor are both required because the filing repeats whole paragraphs
verbatim ("Maharashtra, India", the ICICI Securities contact block) — text alone
cannot identify one, and an index alone would silently drift.

### 2.4 Metrics

Three views, because one number hides the interesting failure:

- **Strict entity matching** — type *and* exact character span must match.
- **Partial entity matching** — type matches and spans overlap, paired
  one-to-one, best overlap first. The operationally meaningful view: if the tool
  redacted two extra words at the start of an address, the personal data still
  got redacted.
- **Token-level accuracy** — every whitespace token classified
  redacted/not-redacted by both gold and system. Unlike entity matching this has
  true negatives, so **accuracy** has a well-defined denominator: all tokens in
  the sample.

Both prospectus suites are scored **in full-document context** — the analyzer
sees all 4,639 paragraphs and the gold ones are sliced back out. That is how the
tool runs in production, and it matters: the propagation pass earns most of its
recall from mentions elsewhere in the file. `--isolated` scores the samples
alone if you want to see the difference.

---

## 3. The held-out result, before and after

This is the part of the report that deserves the most scrutiny, so here is the
full sequence.

**Step 1 — development.** Built the detectors, scored the stratified sample,
found defects, fixed them, repeated. Ended at 1.000 / 1.000 on that sample. At
this point the number is not trustworthy: the sample had become a development
set.

**Step 2 — draw a held-out sample.** 40 paragraphs, random seeds recorded, from
regions never inspected. Labelled by hand. Scored **once**:

| Type | TP | FP | FN | Precision | Recall |
|---|---:|---:|---:|---:|---:|
| ADDRESS | 1 | 0 | 0 | 1.000 | 1.000 |
| EMAIL | 7 | 0 | 0 | 1.000 | 1.000 |
| ORGANIZATION | 1 | **1** | 0 | 0.500 | 1.000 |
| PERSON | 2 | 0 | **1** | 1.000 | 0.667 |
| PHONE | 8 | 0 | **1** | 1.000 | 0.889 |
| URL | 2 | 0 | 0 | 1.000 | 1.000 |
| **Micro** | **21** | **1** | **2** | **0.955** | **0.913** |

Token level: 523 tokens, TP 53, FP 4, FN 7 — accuracy 0.979, precision 0.930,
recall 0.883.

**This is the number to quote.** It is a genuine out-of-sample estimate.

**Step 3 — the three failures, diagnosed.** All three were real defects, not
labelling disputes:

| Failure | Cause | Fix |
|---|---|---|
| FP: `Institute of Chartered Accountants` | The public-body allowlist held `Institute of Chartered Accountants of India`; the legal-suffix rule matched the span without `of India`, so the allowlist missed it | Added the shorter spelling |
| FN: `Cherag Gyara` | The name pattern is greedy and takes up to four title-case tokens. In the run-together cell `Contact Person: Cherag Gyara Website: ...` it grabbed `Cherag Gyara Website Email`, failed the plausibility check, and the whole match was discarded | Trim trailing tokens until what remains is plausible, instead of rejecting outright |
| FN: `+ 91 (20) 6729 5100` | The international phone pattern did not allow parentheses around an area code | Allowed them |

**Step 4 — after fixing, the held-out suite scores 1.000 / 1.000.** That number
is *not* held out any more — the sample has been spent, exactly as a test set is
spent the moment you tune against it. It is reported for completeness only. If
you want one figure for how this tool performs on unseen text from this
document, it is **0.955 precision / 0.913 recall**.

---

## 4. Full results (current build)

### 4.1 Development sample — 56 records, 66 gold entities, 66 predicted

Strict and partial matching are identical here: every predicted span matches its
gold span exactly.

| Type | TP | FP | FN | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|
| ADDRESS | 16 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| DIN | 4 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| EMAIL | 9 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| ORGANIZATION | 11 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| PERSON | 19 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| PHONE | 4 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| URL | 3 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| **Micro** | **66** | **0** | **0** | **1.000** | **1.000** | **1.000** |

Token level: 818 tokens (312 PII, 506 not) — accuracy 1.000.

### 4.2 Synthetic suite — 4 records, 26 gold entities, 25 predicted

Partial matching:

| Type | TP | FP | FN | Precision | Recall |
|---|---:|---:|---:|---:|---:|
| AADHAAR | 1 | 0 | 0 | 1.000 | 1.000 |
| ADDRESS | 2 | 0 | 0 | 1.000 | 1.000 |
| CREDIT_CARD | 3 | 0 | 0 | 1.000 | 1.000 |
| DATE_OF_BIRTH | 3 | 0 | 0 | 1.000 | 1.000 |
| DIN | 1 | 0 | 0 | 1.000 | 1.000 |
| EMAIL | 2 | 0 | 0 | 1.000 | 1.000 |
| IP_ADDRESS | 3 | 0 | 0 | 1.000 | 1.000 |
| ORGANIZATION | 1 | 0 | 0 | 1.000 | 1.000 |
| PAN | 1 | 0 | 0 | 1.000 | 1.000 |
| PASSPORT | 1 | 0 | 0 | 1.000 | 1.000 |
| PERSON | 2 | 0 | 1 | 1.000 | 0.667 |
| PHONE | 3 | 0 | 0 | 1.000 | 1.000 |
| SSN | 2 | 0 | 0 | 1.000 | 1.000 |
| **Micro** | **25** | **0** | **1** | **1.000** | **0.962** |

Token level: 264 tokens — accuracy 0.985.

The decoy record deserves attention. It contains a 16-digit order number, a
`555-12-3456789` ticket id, a `400-25-1000` invoice reference, version strings
`6.011.111.111` and `3.47.2.1`, formatted currency amounts, and the literal
words "PAN" and "Aadhaar" — and **zero entities were predicted in it**. The rules
that achieve this:

- SSN rejects a 3-2-4 number when `invoice`/`order`/`ticket`/`reference`/`PO`
  precedes it, unless an explicit `SSN` label overrides;
- credit cards must pass Luhn **and** carry a real brand prefix;
- IPv4 needs a private/documentation range or two octets above 31, and is vetoed
  by a nearby `version`/`build`/`schema` cue;
- Aadhaar needs a valid Verhoeff check digit;
- DIN and passport need an explicit label or an adjacent director designation.

### 4.3 Whole-document run

| | |
|---|---|
| Input | `Red Herring Prospectus.docx`, 4,639 paragraphs |
| Output | `output/Red Herring Prospectus - REDACTED.docx` (formatting preserved) |
| Runtime | 21 s single core, end to end |
| Entities replaced | **666** |

| Type | Count | Distinct values |
|---|---:|---:|
| PERSON | 264 | 48 |
| ORGANIZATION | 228 | 81 |
| EMAIL | 52 | 26 |
| ADDRESS | 51 | 39 |
| PHONE | 36 | 22 |
| URL | 27 | 15 |
| DIN | 8 | 8 |

ADDRESS has nearly as many distinct values as occurrences because each address
is written slightly differently at each mention — different line breaks,
different abbreviations. That is also why an address surrogate is keyed on its
exact text rather than on a resolved identity, unlike a person's.

Which layer found what:

| Recognizer | Detections | Share |
|---|---:|---:|
| `propagation` | 223 | 33.5% |
| `organization_suffix` | 200 | 30.0% |
| `person_rules` | 59 | 8.9% |
| `email` | 52 | 7.8% |
| `address` | 51 | 7.7% |
| `phone` | 36 | 5.4% |
| `url` | 27 | 4.1% |
| `spacy_ner` | 10 | 1.5% |
| `din_designation` | 8 | 1.2% |

---

## 5. Leak check on the output

After redaction, the output `.docx` was re-scanned for every original
identifier:

| Term | Before | After |
|---|---:|---:|
| `Hegde` (promoter surname) | 103 | **0** |
| `KSH` (issuer acronym) | 31 | **0** |
| `Waterloo` (promoter entity) | 26 | **0** |
| `Shetty` (promoter surname) | 21 | 3 |
| `ICICI` | 17 | **0** |
| `Nuvama` | 13 | **0** |
| `kshinternational` (issuer domain) | 12 | **0** |
| `HDFC` | 11 | **0** |
| `Birdewadi` (factory locality) | 10 | 2 |
| `Kirtane` (auditor) | 9 | **0** |
| `410 501` (registered-office PIN) | 9 | **0** |
| `Malvadkar` (compliance officer) | 6 | **0** |
| `Distriparks` (group company) | 3 | 1 |
| `45053237` (office phone) | 2 | **0** |
| `Trilegal` (legal counsel) | 1 | 1 |
| `Pune` (bare city name) | 49 | 19 — *by design, §8* |

The 30 occurrences of "Pune" that did disappear were inside redacted addresses;
the 19 that remain are bare city mentions in prose, which are deliberately left
alone. The three remaining `Shetty`, two `Birdewadi`, one `Distriparks` and one
`Trilegal` are the known false negatives itemised in §7.

---

## 6. Ablations — did each layer earn its place?

Same gold sets, one component changed. Micro-averaged partial matching.

| Configuration | Development P / R | Held-out P / R | Synthetic P / R |
|---|---|---|---|
| **Default** (rules + propagation + spaCy PERSON) | **1.000 / 1.000** | **1.000 / 1.000** | **1.000 / 0.962** |
| `--no-ner` (rules only, no spaCy) | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 0.885 |
| `--ner-orgs` (trust spaCy's ORG label) | 0.744 / 0.970 | 0.677 / 0.913 | 0.962 / 0.962 |

Whole-document entity counts: default **666**, `--no-ner` **657**,
`--no-propagation` **529**.

**The statistical layer contributes almost nothing on this corpus.** Rules-only
scores identically on both prospectus suites and finds 657 of the 666 entities.
Corporate filings introduce people in structured positions — after an honorific,
before a designation, after a `Contact Person:` label — and rules read those
positions exactly. spaCy earns its keep on free prose: on the synthetic ticket
log it lifts recall from 0.885 to 0.962 by finding names with no structural cue.
It stays on by default for that reason, and `--no-ner` is a supported mode that
drops a ~500 MB dependency and runs in 15 s instead of 21 s.

**Trusting spaCy's ORG label is actively harmful here.** Precision falls to 0.744
on the development sample and 0.677 on the held-out sample — 22 and 10 false
positives respectively — for a *lower* recall, because wrong-shaped ORG spans win
overlap resolution against correct ones. On an early full run it produced
detections like "ASBA Bidder", "Basis of Allotment", "Anchor Investors" and
"Analysis of Financial Condition", which propagation then spread document-wide.
The legal-suffix rule plus short-form propagation covers real company names
without any of that, so `ner_organizations` defaults to **off**. It stays a
config flag rather than a deletion because a corpus of unstructured prose would
flip the trade-off.

**Propagation is the main recall mechanism** — 223 of 666 detections, and
disabling it costs 137 entities (21%), concentrated exactly where privacy risk is
highest: the short forms ("Rajesh Hegde", "KSH", "Nuvama") that appear in running
prose far from any introducing context.

---

## 7. Error analysis

### Remaining false negatives, in full

| Type | Value | Why |
|---|---|---|
| PERSON | `DM Shetty`, `SA Shetty` | Initials plus surname. The known-surname rule needs a capitalised *word* before the surname; accepting `[A-Z]{2}` would match table headers |
| PERSON | `Narayana B. Shetty` | A middle initial sits between the first name and the confirmed surname, so the two-token adjacency rule does not fire |
| PERSON | `Ananya Deshpande` (synthetic) | "KYC review for Ananya Deshpande, employed at …" — no structural cue, and spaCy's small model misses it. The clearest case for upgrading the NER model |
| ORGANIZATION | `Trilegal` | A law firm whose name carries no legal suffix and is never written with one, so nothing seeds the single-token short form |
| ORGANIZATION | `Distriparks Private Limited` | "KSH Distriparks Private Limited" is split across three table cells; `KSH` was redacted via propagation, the remainder spans paragraph boundaries that recognizers deliberately cannot cross |
| ADDRESS | `Chakan Unit No. 2 (Birdewadi)` | A factory unit's name, not a postal address — no street locator, no PIN. Arguably correct to leave |

### Remaining false positives

**None**, on any of the three suites, at either matching mode.

### Defect classes found and fixed during development

Listed because they characterise where this kind of tool goes wrong:

1. **Merged table cells processed twice.** `row.cells` returns the same `<w:tc>`
   once per grid position it spans; this filing yielded 5,715 paragraph visits
   from 4,639 distinct elements. The rewriter therefore applied one paragraph's
   replacements a second time using offsets the first pass had invalidated,
   destroying text (`MEERA MOHAN IYER` → `MEERA MOHAN IYERRA`). Fixed by
   de-duplicating on element identity. **This is the bug that only a real `.docx`
   exposes** — the PDF path never hit it.
2. **Statistical NER on domain text.** `en_core_web_sm` tagged Indian locality and
   society names as `PERSON` ("Deccan Gymkhana", "Buena Monte", "Gram Jyoti") and
   financial jargon as `ORG`. Fixed with a shape predicate (all tokens Title Case
   *or* all CAPS, never mixed), a domain stop-token list, and disabling ORG.
3. **Greedy right-to-left span growth.** The organisation pattern walks left from
   a legal suffix and crossed into the previous company name ("HDFC Bank Limited
   and ICICI Bank Limited" as one entity). Fixed by splitting candidates at
   interior legal suffixes and emitting each part.
4. **Address left boundaries.** Address spans swallowed the phone number and
   company name above them, then later the whole introducing clause ("a company
   incorporated on July 30, 1979 under the Companies Act, 1956 and having its
   Registered Office at …"). Fixed with an explicit boundary set: paragraph
   break, sentence end, field label, legal suffix, e-mail, phone, id column, and
   the phrases that introduce an address.
5. **Word-boundary lookarounds excluding `.`.** Name matching used `(?![\w.@])`
   to avoid matching inside e-mail addresses; this silently rejected every name
   that ended a sentence. Fixed with `(?!\w)(?![.@]\w)`. Invisible without
   per-entity scoring.
6. **Case-sensitive suffix matching.** `FAMILY TRUST` in the all-caps promoter
   list did not match the `Family Trust` suffix; all six promoter trusts were
   missed. Fixed by listing upper-case spellings explicitly rather than using
   `IGNORECASE`, which would have destroyed the capitalisation signal the name
   pattern depends on.
7. **Line-wrap tolerance crossing paragraph boundaries.** The DIN rule allowed a
   wrapped continuation digit; in the `.docx` it swallowed the first digits of
   the *next* table cell (`00114193` + the `12` beginning `12 Buena Monte`). Same
   class of bug in the phone rule. Both now stop at a blank line.
8. **Addresses split across paragraphs.** Word stores each line of an address
   block as its own paragraph, so "…Baner Pune – 411 045" and "Maharashtra,
   India" arrive separately and the first has no geography confirming its PIN.
   Fixed by accepting a PIN that closes a paragraph when introduced by a spaced
   dash, and by a premises-line rule for lines with no postal code at all.
   Adding the first rule immediately produced a false positive on the engineer's
   registration number `M-140388`, which is why the dash must be *spaced*.

---

## 8. Explicit precision choices

The assignment asks us to be explicit about what we chose not to treat as PII.
Each is applied consistently in both the gold labels and the tool, and each is
switchable:

| Not redacted | Reasoning | Override |
|---|---|---|
| Regulator / exchange names (SEBI, BSE, NSE, RBI, RoC, NSDL, CDSL, ICAI) | Public bodies, not private parties. Redacting them destroys a regulatory filing's meaning and protects nobody | `--redact-public-institutions` |
| Government hostnames (`sebi.gov.in`, `bseindia.com`, `*.gov.in`) | Same reasoning, applied to URLs and e-mail domains | same flag |
| CIN, registration number, SEBI registration number, firm registration number | Identify a *filing entity*, not a person | `extra_deny` |
| Order / ticket / invoice / PO numbers | Explicitly called out in the assignment | `extra_deny` |
| Role mailbox local parts (`ipo@`, `customercare@`, `cs.connect@`) | Identify a desk, not a human. The **domain is still replaced**, so the organisation does not leak | — |
| Bare city and state names ("Pune", "Maharashtra") | A city is not a mailing address. Redacting all 49 occurrences of "Pune" would remove ordinary geography for no privacy gain — the full addresses containing them *are* redacted | `extra_deny` |
| Designations ("Independent Director") | A role is not an identifier | — |
| Unlabelled 10-digit numbers | In a filing these are share counts and rupee amounts. Phones need a `+` country code or a `Telephone:`/`Mobile:` label | — |

One deliberately *inclusive* choice: **company websites are redacted**, because a
URL identifies a company exactly as its name does, and redacting the name while
leaving the URL would be theatre.

---

## 9. Limitations

Stated plainly, so the numbers are not read as stronger than they are:

- **The held-out sample is 23 entities.** That is a small basis for a
  precision/recall estimate; the confidence interval around 0.955 / 0.913 is
  wide. Per-type figures there rest on 1–8 instances each.
- **The held-out sample has been spent.** Its three failures were fixed, so
  current scores on it are development scores. A fresh draw would be needed to
  re-estimate.
- **106 gold entities in total** across all three suites. Per-type figures for
  rarer types rest on 1–3 instances.
- **Single annotator, single pass.** No inter-annotator agreement figure. Address
  boundaries in particular are a judgement call, which is why both strict and
  partial matching are reported.
- **The synthetic suite was written by the same person who wrote the rules**, so
  it shows the detectors work on well-formed inputs and resist the specific
  decoys chosen — not that they generalise to arbitrary ticket logs.
- **One document, one domain, one locale.** Everything is measured on an Indian
  corporate filing plus English ticket-log text. Nothing is claimed about medical
  records, non-Latin scripts, or other address formats.
- **Text inside embedded images is not read**; OCR is out of scope.

The honest summary: on unseen text from this document the tool caught 21 of 23
personal-data instances and raised one false positive, all three failures were
diagnosable and have been fixed, and its characteristic residual weakness is a
person named once, with initials or no structural cue, nowhere else in the file.
