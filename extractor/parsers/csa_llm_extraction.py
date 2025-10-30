# csa_extractor.py
import json
import asyncio
import re
import html
from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, Field, validator
from bs4 import BeautifulSoup
from openai import AsyncOpenAI
import os
from dotenv import load_dotenv, find_dotenv

# Ensure .env is discovered from project root when running via CLI/tests
load_dotenv(find_dotenv(), override=True)

# ========================================
# 1. HTML → CLEAN TEXT PREPROCESSOR
# ========================================
def html_to_text(html_content: str) -> str:
    """Convert HTML to clean, readable text. Remove scripts, styles, tables, noise."""
    soup = BeautifulSoup(html_content, "html.parser")

    # Remove unwanted tags
    for tag in soup(["script", "style", "header", "footer", "nav", "aside"]):
        tag.decompose()

    # Extract text
    text = soup.get_text(separator="\n")

    # Clean up
    lines = (line.strip() for line in text.splitlines())
    chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
    text = "\n".join(chunk for chunk in chunks if chunk)

    # Normalize whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" +", " ", text)

    # Unescape HTML entities
    text = html.unescape(text)

    return text.strip()

# ========================================
# 2. PYDANTIC SCHEMA (A/B/C/D)
# ========================================
class RoundingConfig(BaseModel):
    direction: Literal["NEAREST", "UP", "DOWN"]
    amount: float
    currency: str

class HaircutRow(BaseModel):
    asset_type: str
    maturity_bucket: str
    regime: Literal["sp", "m1", "m2", "fitch"]
    valuation_percentage: float

class CapsWindows(BaseModel):
    cash_cap_pct_of_U: Optional[float] = None
    issuer_cap: Optional[str] = None
    class_cap: Optional[str] = None
    currency_cap: Optional[str] = None
    global_cap: Optional[str] = None

class CSAExtraction(BaseModel):
    # A. Document & Parties
    csa: dict = Field(default_factory=dict)
    parties: dict = Field(default_factory=dict)

    @validator("csa")
    def check_csa(cls, v):
        # Ensure meta exists and has expected keys with permissive defaults
        meta = v.get("meta") or {}
        gov = meta.get("governing_law")
        if isinstance(gov, str) and gov not in ["NY", "English", "Japanese", "Ontario", "Singapore", "Other"]:
            gov = None
        # Default missing keys to None
        meta.setdefault("governing_law", gov if gov is not None else None)
        meta.setdefault("agreement_date", meta.get("agreement_date", None))
        ow = meta.get("one_way")
        if ow not in [True, False, None]:
            ow = None
        meta.setdefault("one_way", ow if ow is not None else None)
        v["meta"] = meta
        return v

    @validator("parties")
    def check_parties(cls, v):
        pa = v.get("party_A") or {}
        pb = v.get("party_B") or {}
        # Ensure keys exist
        pa.setdefault("name", pa.get("name", None))
        pb.setdefault("name", pb.get("name", None))
        ra = pa.get("role")
        rb = pb.get("role")
        # Normalize common role phrasings
        def norm_role(x):
            if x is None:
                return None
            if not isinstance(x, str):
                return None
            xl = x.strip().lower()
            if xl.startswith("secured"):
                return "Secured"
            if xl.startswith("pledgor"):
                return "Pledgor"
            if xl.startswith("both"):
                return "Both"
            return None
        if ra not in ["Pledgor", "Secured", "Both", None]:
            ra = norm_role(ra)
        if rb not in ["Pledgor", "Secured", "Both", None]:
            rb = norm_role(rb)
        if ra not in ["Pledgor", "Secured", "Both", None]:
            ra = None
        if rb not in ["Pledgor", "Secured", "Both", None]:
            rb = None
        pa.setdefault("role", ra if ra is not None else None)
        pb.setdefault("role", rb if rb is not None else None)
        v["party_A"] = pa
        v["party_B"] = pb
        return v

    # B. Core VM Mechanics
    terms: dict = Field(default_factory=dict)

    @validator("terms")
    def check_terms(cls, v):
        # MTA
        if "mta" in v:
            amt = v["mta"].get("amount")
            cur = v["mta"].get("currency")
            if not ((amt is None) or isinstance(amt, (int, float))):
                v["mta"]["amount"] = None
            if isinstance(cur, str):
                if cur.strip() in ["$", "US$"]:
                    v["mta"]["currency"] = "USD"
                elif len(cur) != 3:
                    v["mta"]["currency"] = None
            else:
                v["mta"]["currency"] = None
        else:
            v["mta"] = {"amount": None, "currency": None}

        # Rounding
        if "rounding" in v:
            for key in ["delivery", "return"]:
                entry = v["rounding"].get(key)
                if entry is None:
                    continue
                if isinstance(entry, dict):
                    # Validate expected shape
                    RoundingConfig(**entry)
                else:
                    # Coerce non-dict (e.g., string) to None to avoid validation errors
                    v["rounding"][key] = None

        # Return timing
        if "return_timing" in v:
            days = v["return_timing"].get("days")
            if not ((days is None) or isinstance(days, int)):
                v["return_timing"]["days"] = None
        else:
            v["return_timing"] = {"days": None}

        return v

    # C. Currencies & FX
    @validator("terms")
    def check_currencies(cls, v):
        if "base_currency" in v:
            bc = v.get("base_currency")
            if not ((bc is None) or (isinstance(bc, str) and len(bc) == 3)):
                v["base_currency"] = None
        if "eligible_currencies" in v:
            lst = v.get("eligible_currencies")
            if lst is None:
                v["eligible_currencies"] = []
            else:
                if not isinstance(lst, list):
                    v["eligible_currencies"] = []
                else:
                    v["eligible_currencies"] = [c for c in lst if isinstance(c, str) and len(c) == 3]
        else:
            v["eligible_currencies"] = []
        if "eligible_currency_includes_base" in v:
            if v["eligible_currency_includes_base"] not in [True, False, None]:
                v["eligible_currency_includes_base"] = None
        if "fx_haircut_pct" in v:
            if not (isinstance(v["fx_haircut_pct"], (int, float)) or v["fx_haircut_pct"] is None):
                v["fx_haircut_pct"] = None
        return v

    # D. Eligible Collateral & Haircuts
    eligibility: dict = Field(default_factory=dict)
    haircuts: dict = Field(default_factory=dict)
    caps_windows: Optional[CapsWindows] = None

    @validator("eligibility")
    def check_eligibility(cls, v):
        if "covered_transactions" in v:
            if v["covered_transactions"] is None:
                v["covered_transactions"] = []
            else:
                assert isinstance(v["covered_transactions"], list)
        else:
            v["covered_transactions"] = []
        if "spot_fx_carveout" in v:
            assert v["spot_fx_carveout"] in [True, False, None]
        return v

    @validator("haircuts")
    def check_haircuts(cls, v):
        if "matrix" in v:
            if v["matrix"] is None:
                v["matrix"] = []
            else:
                assert isinstance(v["matrix"], list)
                cleaned = []
                for row in v["matrix"]:
                    if not isinstance(row, dict):
                        continue
                    required = {"asset_type", "maturity_bucket", "regime", "valuation_percentage"}
                    if not required.issubset(row.keys()):
                        continue
                    try:
                        HaircutRow(**row)
                        cleaned.append(row)
                    except Exception:
                        continue
                v["matrix"] = cleaned
        else:
            v["matrix"] = []
        return v

    @validator("caps_windows")
    def check_caps(cls, v):
        if v:
            CapsWindows(**v.dict(exclude_none=True))
        return v

# ========================================
# 3. FIELD LIST & MAX TOKENS
# ========================================
FIELDS = [
    "csa.meta.governing_law", "csa.meta.agreement_date", "csa.meta.one_way",
    "parties.party_A.name", "parties.party_B.name", "parties.party_A.role", "parties.party_B.role",
    "terms.valuation_agent", "terms.notification_time", "terms.valuation_date", "terms.valuation_time",
    "terms.regular_settlement_day", "terms.delivery_amount", "terms.return_amount",
    "terms.mta.amount", "terms.mta.currency", "terms.rounding.delivery", "terms.rounding.return",
    "terms.dispute.notice_cutoff", "terms.dispute.resolution_timing", "terms.return_timing.days",
    "terms.base_currency", "terms.eligible_currencies", "terms.eligible_currency_includes_base", "terms.fx_haircut_pct",
    "eligibility.covered_transactions", "eligibility.spot_fx_carveout", "eligibility.ratings_condition",
    "eligibility.issuer_constraints", "csa.regime.default", "haircuts.matrix",
    "caps_windows.cash_cap_pct_of_U", "caps_windows.issuer_cap", "caps_windows.class_cap",
    "caps_windows.currency_cap", "caps_windows.global_cap"
]

MAX_TOKENS = {
    "csa.meta.governing_law": 100, "csa.meta.agreement_date": 100, "csa.meta.one_way": 50,
    "parties.party_A.name": 200, "parties.party_B.name": 200, "parties.party_A.role": 50, "parties.party_B.role": 50,
    "terms.valuation_agent": 100, "terms.notification_time": 200, "terms.valuation_date": 300,
    "terms.valuation_time": 200, "terms.regular_settlement_day": 200,
    "terms.mta.amount": 50, "terms.mta.currency": 50,
    "terms.rounding.delivery": 300, "terms.rounding.return": 300,
    "terms.dispute.notice_cutoff": 200, "terms.dispute.resolution_timing": 300,
    "terms.return_timing.days": 50, "terms.base_currency": 50,
    "terms.eligible_currencies": 200, "terms.eligible_currency_includes_base": 50,
    "terms.fx_haircut_pct": 50, "eligibility.covered_transactions": 300,
    "eligibility.spot_fx_carveout": 50, "eligibility.ratings_condition": 1000,
    "eligibility.issuer_constraints": 1000, "csa.regime.default": 100,
    "caps_windows.cash_cap_pct_of_U": 100, "caps_windows.issuer_cap": 200,
    "caps_windows.class_cap": 200, "caps_windows.currency_cap": 200, "caps_windows.global_cap": 200,
    "terms.delivery_amount": 2000, "terms.return_amount": 2000, "haircuts.matrix": 4000,
}

# ========================================
# 4. PROMPTS (generic, no examples)
# ========================================
PROMPT_TEMPLATES = {
    "csa.meta.governing_law": """
Extract ONLY the primary governing law.
Look for: "governed by", "English law", "New York law", "Japanese law".
Return one of: "NY", "English", "Japanese", "Ontario", "Singapore", "Other".
If mixed → first mentioned.
If not found → null.
Return ONLY JSON.
""",
    "haircuts.matrix": (
        "Extract ONLY from Paragraph 13(c)(ii) 'Eligible Collateral (VM)' table.\n"
        "For each row, output objects with: asset_type (left-most column, full text), maturity_bucket (row sub-header), valuation_percentage (float), regime (column header, normalized).\n"
        "REGIME MAPPING: 'S&P' or 'Standard & Poor’s' => 'sp'; 'Moody’s First Trigger' or 'Moody’s (1st)' => 'm1'; 'Moody’s Second Trigger' or 'Moody’s (2nd)' => 'm2'; 'Fitch' => 'fitch'.\n"
        "RULES: If table has one column -> use 'csa.regime.default' if set, else null. If multiple columns -> match cell to column header. If header is above the table -> use it. If no header -> regime: null. If regime not in ['sp','m1','m2','fitch'] -> null. Include ALL rows.\n"
        "Return ONLY JSON with key 'haircuts.matrix'."
    ),
}

GENERIC_PROMPT = """
Extract the requested field from the CSA text. Use only explicit statements; if not present, return null (or []).
Return ONLY strict JSON with the single key provided.
"""

# ========================================
# 5. BATCH EXTRACTOR + VALIDATION
# ========================================
async def extract_field(field: str, document: str, client: AsyncOpenAI) -> Dict[str, Any]:
    tmpl = PROMPT_TEMPLATES.get(field, GENERIC_PROMPT)
    # Avoid nested brace escaping; instruct clearly
    prompt = f"{tmpl}\nReturn ONLY JSON with key \"{field}\"."
    max_tokens = MAX_TOKENS.get(field, 800)

    try:
        response = await client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o"),
            messages=[
                {"role": "system", "content": "Extract ONLY. No explanation. Handle multi-regime CSA."},
                {"role": "user", "content": f"{prompt}\n\nDocument:\n{document[:30000]}"}
            ],
            temperature=0.0,
            max_tokens=max_tokens
        )
        raw = response.choices[0].message.content.strip()
        # Try strict JSON first
        try:
            return json.loads(raw)
        except Exception:
            # Strip code fences / language hints
            txt = raw.strip()
            if txt.startswith("```"):
                txt = txt.strip("`\n ")
                nl = txt.find("\n")
                if nl != -1:
                    txt = txt[nl+1:]
            # Gracefully handle 'null' or 'None'
            if txt.lower() in ("null", "none"):
                return {field: None}
            # Extract first JSON-looking object
            import re as _re
            m = _re.search(r"\{[\s\S]*\}", txt)
            if m:
                try:
                    return json.loads(m.group(0))
                except Exception:
                    return {field: None}
            return {field: None}
    except Exception:
        return {field: None}

async def extract_csa(html_content: str) -> CSAExtraction:
    # Lazy-initialize client using env API key; if missing, return an empty-but-valid structure
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return CSAExtraction(
            csa={"meta": {"governing_law": None, "agreement_date": None, "one_way": None}},
            parties={"party_A": {"name": None, "role": None}, "party_B": {"name": None, "role": None}},
            terms={}, eligibility={}, haircuts={}, caps_windows=CapsWindows()
        )
    client = AsyncOpenAI(api_key=api_key)
    text = html_to_text(html_content)
    tasks = [extract_field(field, text, client) for field in FIELDS]
    results = await asyncio.gather(*tasks)
    raw = {k: v for d in results for k, v in d.items()}

    # Build nested dict
    data = {
        "csa": {"meta": {}},
        "parties": {"party_A": {}, "party_B": {}},
        "terms": {},
        "eligibility": {},
        "haircuts": {},
        "caps_windows": {}
    }

    for k, v in raw.items():
        parts = k.split(".")
        current = data
        for p in parts[:-1]:
            if p not in current:
                current[p] = {}
            current = current[p]
        current[parts[-1]] = v

    # Validate with normalization fallback
    try:
        return CSAExtraction(**data)
    except Exception:
        # Normalize common issues and retry once
        terms = data.get("terms", {})
        # Fix rounding keys
        rnd = terms.get("rounding")
        if isinstance(rnd, dict):
            for key in ["delivery", "return"]:
                entry = rnd.get(key)
                if isinstance(entry, dict):
                    if "direction" not in entry and "mode" in entry:
                        entry["direction"] = entry.pop("mode").upper()
        # Ensure eligible_currencies list
        if terms.get("eligible_currencies") is None:
            terms["eligible_currencies"] = []
        # Normalize MTA currency
        mta = terms.get("mta")
        if isinstance(mta, dict):
            cur = mta.get("currency")
            if isinstance(cur, str):
                if cur.strip() in ["$", "US$"]:
                    mta["currency"] = "USD"
                elif len(cur) != 3:
                    mta["currency"] = None
        data["terms"] = terms
        return CSAExtraction(**data)


async def extract_csa_fields(html_content: str, fields: List[str]) -> CSAExtraction:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return CSAExtraction(
            csa={"meta": {"governing_law": None, "agreement_date": None, "one_way": None}},
            parties={"party_A": {"name": None, "role": None}, "party_B": {"name": None, "role": None}},
            terms={}, eligibility={}, haircuts={}, caps_windows=CapsWindows()
        )
    client = AsyncOpenAI(api_key=api_key)
    text = html_to_text(html_content)
    tasks = [extract_field(field, text, client) for field in fields]
    results = await asyncio.gather(*tasks)
    raw = {k: v for d in results for k, v in d.items()}

    data = {
        "csa": {"meta": {}},
        "parties": {"party_A": {}, "party_B": {}},
        "terms": {},
        "eligibility": {},
        "haircuts": {},
        "caps_windows": {}
    }
    for k, v in raw.items():
        parts = k.split(".")
        current = data
        for p in parts[:-1]:
            if p not in current:
                current[p] = {}
            current = current[p]
        current[parts[-1]] = v
    return CSAExtraction(**data)

# ========================================
# 6. USAGE
# ========================================
if __name__ == "__main__":
    with open("csa.html", "r", encoding="utf-8") as f:
        html = f.read()

    result = asyncio.run(extract_csa(html))
    print(json.dumps(result.dict(), indent=2, ensure_ascii=False))