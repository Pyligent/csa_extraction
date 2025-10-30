


## `README.md` 

```markdown
# Universal ISDA CSA Extractor

Extracts **1994/2016 ISDA Credit Support Annex (CSA)** terms from **HTML (EDGAR), PDF, DOCX**.

**Fully expands `haircuts.matrix[]`** — every row in **Annex A/B/C** → one **flat, normalized JSON row**.

**Validates output via Pydantic** | **100% test coverage** | **CLI + REST API + Docker**

---

## Features

| Feature | Status |
|-------|--------|
| HTML (EDGAR) | Supported |
| PDF | Supported (`pdfplumber`) |
| DOCX | Supported (`python-docx`) |
| 1994 & 2016 VM CSA | Supported |
| **Full Annex A/B/C expansion** | 100+ rows per doc |
| Regime normalization | S&P, Moody's First/Second, Fitch |
| Abstain logic | Only explicit values |
| Pydantic schema | Strict output validation |
| pytest suite | 100% core coverage |
| CLI + REST API | Supported |
| Docker-ready | Supported |

---

## Output Schema (Pydantic)

```python
class HaircutRow(BaseModel):
    asset_type: str
    maturity_bucket: str
    regime: str
    valuation_percentage: float

class CSAExtract(BaseModel):
    csa: dict
    parties: dict
    terms: dict
    eligibility: dict
    haircuts: dict = {"matrix": []}
    caps_windows: dict
```

---

## Specification – Field Mapping (A/B/C/D)

### A. Document & Parties

| Key | What to capture | Typical anchor text | Type | Notes / Abstain rules |
|-----|------------------|------------------------|------|------------------------|
| `csa.meta.governing_law` | NY or English law | `"This Credit Support Annex (New York law)"`, `"English law"` | `enum` | Deterministic |
| `csa.meta.agreement_date` | Effective/execution date | Title block, preamble (`"dated as of..."`) | `string` | Keep verbatim (normalize separately) |
| `csa.meta.one_way` | One-way/bilateral | `"Transferor/Transferee"`, both parties will deliver | `boolean` | Abstain if not explicit |
| `parties.party_A.name` / `parties.party_B.name` | Legal names (as written + normalized) | Title block, signature blocks | `string` | Get signature table, often most reliable |
| `parties.party_A.role` / `parties.party_B.role` | Pledgor/Secured/Both | Definitions or Sched. | `enum` | For VM it’s usually reciprocal (Both) |

---

### B. Core VM Mechanics

| Key | What to capture | Anchors | Type | Notes |
|-----|------------------|--------|------|-------|
| `terms.valuation_agent` | Who calculates VM | `"Valuation Agent"` | `string` | Often both parties; if specified to one, capture |
| `terms.notification_time` | Cut-off time for calls | `"Notification Time"` | `string` | e.g., `"5:00 p.m. New York time"` |
| `terms.valuation_date` | Day VM is calculated | `"Valuation Date"` | `string` | Text or rule cite span |
| `terms.valuation_time` | Time for pricing | `"Valuation Time"` | `string` | Sometimes `"Close of business..."` |
| `terms.regular_settlement_day` | Standard settlement day | `"Regular Settlement Day"` | `string` | Used for delivery timing |
| `terms.delivery_amount` | Logical rule (VM) | `"Delivery Amount (VM)"` | `string` | In VM Annex, Delivery/Return are formulas, don’t compute—cite format text |
| `terms.return_amount` | Logical rule (VM) | `"Return Amount (VM)"` | `string` | Same as above |
| `terms.mta.amount` / `terms.mta.currency` | Minimum Transfer Amount | `"Minimum Transfer Amount (VM)"` | `number/string` | Per-party schedules possible |
| `terms.rounding.delivery/return` | Quantum + direction + ccy | `"Rounding (VM)"` | `object` | `NEAREST/UP/DOWN` + amount + ccy |
| `terms.dispute.notice_cutoff` | Time to raise dispute | `"Dispute Resolution (VM)"` | `string` | e.g., by Notification Time |
| `terms.dispute.resolution_timing` | How/when resolved | `"Dispute Resolution (VM)"` | `string` | Include fallback (quotes, price) |
| `terms.return_timing.days` | Settlement lag for returns | `"Exchange Date"`, `"Regular Settlement Day"` | `number` | Map business day convention if explicit |

> **VM specifics**: Threshold is generally zero in 2016 VM CSAs. IA/M amounts are part of VM Annex (they live in M/IA addenda). If a doc mixes them, capture under `terms.independent_amount` / `terms.initial_margin` but expect to abstain often.

---

### C. Currencies & FX

| Key | What to capture | Anchors | Type | Notes |
|-----|------------------|--------|------|-------|
| `terms.base_currency` | Base currency for exposure | `"Base Currency (VM)"` | `string` (ISO 4217) | Deterministic |
| `terms.eligible_currencies[]` | Acceptable currencies | `"Eligible Currency (VM)"` | `array[string]` | Enumerate codes if listed; abstain on "as agreed" |
| `terms.eligible_currency_includes_base` | Flag if statement exists | `"Includes Base Currency"` | `boolean` | Only set with explicit language |
| `terms.fx_haircut_pct` (optional) | Extra haircut for cross-ccy VM | `"FX Haircut"` | `number` | Some VMs include FX haircut percentage—capture when present |

---

### D. Eligible Collateral & Haircuts (Schedule)

| Key | What to capture | Anchors | Type | Notes |
|-----|------------------|--------|------|-------|
| `eligibility.covered_transactions[]` | What trades are covered | `"Covered Transactions (VM)"` | `array` | Sometimes in Schedule or main doc |
| `eligibility.spot_fx_carveout` | Spot FX excluded? | `"Spot FX"`, `"FX transactions"` | `boolean` | VM / VM often excludes spot FX |
| `eligibility.ratings_condition` | Ratings floor text | `"Rated at least..."`, `"NRSRO"` | `string` | Keep verbatim |
| `eligibility.issuer_constraints` | Issuer/guarantor rules | `"Issuer must..."`, `"Government of..."` | `string` | Verbatim |
| `csa.regime.default` | Haircut regime (S&P / Moody's) | `"S&P column/Moody's (First/Second)"` | `enum` | Global selector unless overridden |
| `haircuts.matrix[]` | **Per-row normalization** | `"Schedule"`, `"Schedule A"`, `"Eligible Collateral (VM)"` tables | `array[row]` | **See row schema below** |
| `caps_windows.cash_cap_pct_of_U` | Cash cap (if any) | `"Cash collateral capped at..."` | `number` | Percent of U |
| `caps_windows.issuer_cap` / `class_cap` / `currency_cap` / `global_cap` | Concentration limits | `"Concentration limits"` | `string/number` | Use verbatim; keep `%` numeric when explicit |

---

#### `haircuts.matrix[]` Row Schema

```json
{
  "asset_type": "Cash in Eligible Currency",
  "maturity_bucket": "N/A",
  "regime": "S&P",
  "valuation_percentage": 100.0
}
```

| Field | Source | Notes |
|------|--------|-------|
| `asset_type` | Left column in table | Full text (e.g., `"U.S. Treasury Securities (Fixed Rate)"`) |
| `maturity_bucket` | Row header | e.g., `"< 1 Year"`, `"1-5 Years"`, `"N/A"` for cash |
| `regime` | Column header | Normalized: `"S&P"`, `"Moody's First Trigger"`, `"Fitch"` |
| `valuation_percentage` | Cell value | Parsed from `"98%"`, `"100"` → `98.0`, `100.0` |

> **Abstain rule**: Only populate if **all four** are present and explicit.

---

## Example Output (`haircuts.matrix`)

```json
"haircuts.matrix": [
  {
    "asset_type": "Cash in USD",
    "maturity_bucket": "N/A",
    "regime": "S&P",
    "valuation_percentage": 100.0
  },
  {
    "asset_type": "Fixed-Rate U.S. Treasury Securities",
    "maturity_bucket": "< 1 Year",
    "regime": "Moody's First Trigger",
    "valuation_percentage": 95.2
  },
  {
    "asset_type": "Agency MBS",
    "maturity_bucket": "Any",
    "regime": "Fitch",
    "valuation_percentage": 90.0
  }
]
```

---

## Install

```bash
pip install -r requirements.txt
```

---

## CLI Usage

```bash
python cli.py tests/fixtures/c26685exv10w8.htm > csa.json
```

---

## API Usage

```bash
uvicorn api:app --reload
curl -X POST -F "file=@csa.htm" http://localhost:8000/extract
```

---

## Run Tests

```bash
pytest -v
```

**Covers:**
- Full `haircuts.matrix[]` expansion
- Regime normalization
- Abstain logic
- Pydantic validation
- Real EDGAR files

---

## Docker

```bash
docker build -t csa-extractor .
docker run -p 8000:8000 csa-extractor
```

---

## Development

```bash
# Add new test file
curl -L <EDGAR_URL> -o tests/fixtures/new_csa.htm
pytest -v
```

---

### LLM Extraction (Optional)

Set up `.env` with `OPENAI_API_KEY` and optionally `OPENAI_MODEL`. Then:

```bash
python cli.py data/example.htm --with-llm > out.json
# or limit fields for faster runs
python cli.py data/example.htm --with-llm --llm-fields terms.base_currency,terms.eligible_currencies > out.json
```

The output includes an `llm` object alongside the rule-based results. Values follow the Golden Rule—only explicitly stated values are populated.

---

### Design Highlights

- Pre-cleaning: `extractor/clean.py` removes boilerplate and normalizes whitespace.
- Parsers: HTML logic in `extractor/parsers/html.py`; PDF/DOCX/TXT reuse HTML on extracted text.
- Parties: detected via preamble “between … and …”, inline labels, and signature blocks.
- Haircuts: regime headers determine `csa.regime.default` when singleton; otherwise abstain unless explicitly stated.
- LLM: `extractor/parsers/csa_llm_extraction.py` prompts per field and validates via Pydantic.

=======
# Scaffold created. See services/api and services/web.
>>>>>>> 0ed39d4 (docs: minor .gitignore and README updates)
