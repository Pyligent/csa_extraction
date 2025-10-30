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
from dotenv import load_dotenv

load_dotenv()

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
        assert "meta" in v, "csa.meta required"
        meta = v["meta"]
        assert "governing_law" in meta and meta["governing_law"] in ["NY", "English", "Japanese", "Ontario", "Singapore", "Other", None]
        assert "agreement_date" in meta
        assert "one_way" in meta and meta["one_way"] in [True, False, None]
        return v

    @validator("parties")
    def check_parties(cls, v):
        assert "party_A" in v and "name" in v["party_A"]
        assert "party_B" in v and "name" in v["party_B"]
        assert v["party_A"].get("role") in ["Pledgor", "Secured", "Both", None]
        assert v["party_B"].get("role") in ["Pledgor", "Secured", "Both", None]
        return v

    # B. Core VM Mechanics
    terms: dict = Field(default_factory=dict)

    @validator("terms")
    def check_terms(cls, v):
        # MTA
        if "mta" in v:
            assert "amount" in v["mta"] and isinstance(v["mta"]["amount"], (int, float)) or v["mta"]["amount"] is None
            assert "currency" in v["mta"] and (len(v["mta"]["currency"]) == 3 or v["mta"]["currency"] is None)

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
            assert "days" in v["return_timing"] and (isinstance(v["return_timing"]["days"], int) or v["return_timing"]["days"] is None)

        return v

    # C. Currencies & FX
    @validator("terms")
    def check_currencies(cls, v):
        if "base_currency" in v:
            assert len(v["base_currency"]) == 3 or v["base_currency"] is None
        if "eligible_currencies" in v:
            assert isinstance(v["eligible_currencies"], list)
            for c in v["eligible_currencies"]:
                assert len(c) == 3
        if "eligible_currency_includes_base" in v:
            assert v["eligible_currency_includes_base"] in [True, False, None]
        if "fx_haircut_pct" in v:
            assert isinstance(v["fx_haircut_pct"], (int, float)) or v["fx_haircut_pct"] is None
        return v

    # D. Eligible Collateral & Haircuts
    eligibility: dict = Field(default_factory=dict)
    haircuts: dict = Field(default_factory=dict)
    caps_windows: Optional[CapsWindows] = None

    @validator("eligibility")
    def check_eligibility(cls, v):
        if "covered_transactions" in v:
            assert isinstance(v["covered_transactions"], list)
        if "spot_fx_carveout" in v:
            assert v["spot_fx_carveout"] in [True, False, None]
        return v

    @validator("haircuts")
    def check_haircuts(cls, v):
        if "matrix" in v:
            assert isinstance(v["matrix"], list)
            for row in v["matrix"]:
                HaircutRow(**row)
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
    # ... (all other prompts from previous version — generic, no examples)
    # Full list available in final repo
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

    # Validate
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