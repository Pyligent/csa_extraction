Below is the **complete, production-ready extraction guideline** for **every field in your CSA schema** — **100% aligned with your A/B/C/D spec tables**, **abstain logic**, **real-world anchors**, and **deterministic rules**.

This is the **single source of truth** for:
- Manual review
- LLM prompting
- Parser implementation
- QA validation

---

# **Universal ISDA CSA Extraction Guideline**  
*(1994/2016 VM — HTML, PDF, DOCX)*

> **Golden Rule**:  
> **Only extract if *explicitly stated*.**  
> **If not found → `null`, `[]`, or `false`.**  
> **Never infer. Never assume.**

---

## **A. Document & Parties**

| Key | What to capture | Typical anchor text | Type | Notes / Abstain rules |
|-----|------------------|------------------------|------|------------------------|
| `csa.meta.governing_law` | NY or English law | `"This Credit Support Annex (New York law)"`, `"English law"` | `enum: "NY" \| "English"` | Deterministic |
| `csa.meta.agreement_date` | Effective/execution date | Title block, preamble (`"dated as of..."`) | `string` | Keep verbatim (normalize separately) |
| `csa.meta.one_way` | One-way/bilateral | `"Pledgor/Secured"`, `"both parties will deliver"` | `boolean` | Abstain if not explicit |
| `parties.party_A.name` / `parties.party_B.name` | Legal names (as written) | Title block, signature blocks | `string` | **Best source: signature table** |
| `parties.party_A.normalized_name` / `parties.party_B.normalized_name` | Cleaned name | — | `string` | Title case, remove "Inc.", "LLC" if redundant |
| `parties.party_A.role` / `parties.party_B.role` | Pledgor/Secured/Both | Definitions or Schedule | `enum: "Pledgor" \| "Secured" \| "Both"` | For VM: usually `Both` |

---

## **B. Core VM Mechanics**

| Key | What to capture | Anchors | Type | Notes |
|-----|------------------|--------|------|-------|
| `terms.valuation_agent` | Who calculates VM | `"Valuation Agent"` | `string` | Often both; capture one if specified |
| `terms.notification_time` | Cut-off time for calls | `"Notification Time"` | `string` | e.g., `"5:00 p.m. New York time"` |
| `terms.valuation_date` | Day VM is calculated | `"Valuation Date"` | `string` | Text or rule cite span |
| `terms.valuation_time` | Time for pricing | `"Valuation Time"` | `string` | Sometimes `"Close of business..."` |
| `terms.regular_settlement_day` | Standard settlement day | `"Regular Settlement Day"` | `string` | Used for delivery timing |
| `terms.delivery_amount` | Logical rule (VM) | `"Delivery Amount (VM)"` | `string` | **Do not compute** — cite formula text |
| `terms.return_amount` | Logical rule (VM) | `"Return Amount (VM)"` | `string` | Same as above |
| `terms.mta.amount` / `terms.mta.currency` | Minimum Transfer Amount | `"Minimum Transfer Amount (VM)"` | `number/string` | Per-party possible |
| `terms.rounding.delivery` | Quantum + direction + ccy | `"Rounding (VM)"` | `object` | `{ "direction": "NEAREST", "amount": 10000, "currency": "USD" }` |
| `terms.rounding.return` | Same for returns | `"Rounding (VM)"` | `object` | Same |
| `terms.dispute.notice_cutoff` | Time to raise dispute | `"Dispute Resolution (VM)"` | `string` | e.g., `"by Notification Time"` |
| `terms.dispute.resolution_timing` | How/when resolved | `"Dispute Resolution (VM)"` | `string` | Include fallback (quotes, price) |
| `terms.return_timing.days` | Settlement lag for returns | `"Exchange Date"`, `"Regular Settlement Day"` | `number` | Map business day convention |

> **VM Note**: Threshold = 0 in 2016 VM. IA/M in M/IA addenda. Abstain if mixed.

---

## **C. Currencies & FX**

| Key | What to capture | Anchors | Type | Notes |
|-----|------------------|--------|------|-------|
| `terms.base_currency` | Base currency for exposure | `"Base Currency (VM)"` | `string` (ISO 4217) | Deterministic |
| `terms.eligible_currencies[]` | Acceptable currencies | `"Eligible Currency (VM)"` | `array[string]` | Enumerate codes; abstain on "as agreed" |
| `terms.eligible_currency_includes_base` | Flag if statement exists | `"Includes Base Currency"` | `boolean` | **Only set with explicit language** |
| `terms.fx_haircut_pct` | Extra haircut for cross-ccy VM | `"FX Haircut"` | `number` | Some VMs include — capture when present |

---

## **D. Eligible Collateral & Haircuts (Schedule)**

| Key | What to capture | Anchors | Type | Notes |
|-----|------------------|--------|------|-------|
| `eligibility.covered_transactions[]` | What trades are covered | `"Covered Transactions (VM)"` | `array` | Sometimes in Schedule |
| `eligibility.spot_fx_carveout` | Spot FX excluded? | `"Spot FX"`, `"FX transactions"` | `boolean` | VM often excludes spot FX |
| `eligibility.ratings_condition` | Ratings floor text | `"Rated at least..."`, `"NRSRO"` | `string` | Keep verbatim |
| `eligibility.issuer_constraints` | Issuer/guarantor rules | `"Issuer must..."`, `"Government of..."` | `string` | Verbatim |
| `csa.regime.default` | Haircut regime (S&P / Moody's) | `"S&P column/Moody's (First/Second)"` | `enum: "sp" \| "m1" \| "m2" \| "fitch"` | Global selector unless overridden |
| `haircuts.matrix[]` | **Per-row normalization** | `"Schedule"`, `"Eligible Collateral (VM)"` tables | `array[row]` | **See row schema below** |
| `caps_windows.cash_cap_pct_of_U` | Cash cap (if any) | `"Cash collateral capped at..."` | `number` | Percent of U |
| `caps_windows.issuer_cap` / `class_cap` / `currency_cap` / `global_cap` | Concentration limits | `"Concentration limits"` | `string/number` | Use verbatim; keep `%` numeric when explicit |

---

### **`haircuts.matrix[]` Row Schema**

```json
{
  "asset_type": "Cash in Eligible Currency",
  "maturity_bucket": "N/A",
  "regime": "sp",
  "valuation_percentage": 100.0
}
```

| Field | Source | Notes |
|------|--------|-------|
| `asset_type` | Left column in table | Full text (e.g., `"U.S. Treasury Securities (Fixed Rate)"`) |
| `maturity_bucket` | Row header | e.g., `"< 1 Year"`, `"1-5 Years"`, `"N/A"` for cash |
| `regime` | Column header | Normalized: `"S&P"` → `"sp"`, `"Moody's First Trigger"` → `"m1"` |
| `valuation_percentage` | Cell value | Parsed from `"98%"`, `"100"` → `98.0`, `100.0` |

> **Abstain rule**: Only populate if **all four** are present and explicit.

---

## **Regime Mapping**

| Column Header | Code |
|--------------|------|
| `S&P` | `"sp"` |
| `Moody's First Trigger` | `"m1"` |
| `Moody's Second Trigger` | `"m2"` |
| `Fitch` | `"fitch"` |

> **Default logic**:
> - 1 column → that is default
> - Multiple → look for `"S&P column"` or `"review by S&P"` → `"sp"`
> - Else → `null`

---

## **Abstain Examples**

```json
{
  "terms.dispute.notice_cutoff": null,
  "terms.return_timing.days": null,
  "terms.base_currency": null,
  "terms.eligible_currencies": [],
  "terms.eligible_currency_includes_base": null,
  "terms.fx_haircut_pct": null,
  "eligibility.covered_transactions": [],
  "eligibility.spot_fx_carveout": null,
  "csa.regime.default": null,
  "haircuts.matrix": []
}
```

---

## **Example Output (Partial)**

```json
{
  "csa.meta.governing_law": "NY",
  "csa.meta.agreement_date": "June 19, 2008",
  "csa.meta.one_way": true,
  "parties.party_A.name": "WACHOVIA BANK, NATIONAL ASSOCIATION",
  "parties.party_B.role": "Pledgor",
  "terms.base_currency": "USD",
  "terms.eligible_currencies": ["USD"],
  "terms.eligible_currency_includes_base": true,
  "terms.fx_haircut_pct": 8.0,
  "eligibility.covered_transactions": ["All Transactions"],
  "eligibility.spot_fx_carveout": true,
  "csa.regime.default": "sp",
  "haircuts.matrix": [
    {
      "asset_type": "Fixed-Rate U.S. Treasury Securities",
      "maturity_bucket": "< 1 Year",
      "regime": "sp",
      "valuation_percentage": 98.8
    }
  ]
}
```

---

## **Production Parser Tips**

```python
# Use anchors + context windows
def _extract(text, anchors, stop_anchors):
    idx = find_anchor(text, anchors)
    if not idx: return None
    return extract_paragraph(text, idx, stop_anchors)

# Haircut table parsing
for table in soup.find_all('table'):
    headers = [th.get_text() for th in table.find_all('th')]
    if any(r in " ".join(headers) for r in ["S&P", "Moody's", "Fitch"]):
        parse_haircut_table(table, result)
```

