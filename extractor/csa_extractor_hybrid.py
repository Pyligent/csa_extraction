import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup

from .parsers.html import parse_html as rule_parse_html
from .parsers import csa_llm_extraction as llm_mod

logger = logging.getLogger(__name__)


@dataclass
class ExtractionResult:
    value: Any
    source: str  # "regex" or "llm"
    confidence: float  # 0.0-1.0
    error: Optional[str] = None


def preprocess_html(html: str) -> str:
    """Convert messy EDGAR HTML to clean, searchable text (lightweight)."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "head", "meta", "title"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    # collapse whitespace
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    return text


def _flatten_result_keys(d: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten nested dict into dotted keys to align with LLM field names."""
    flat: Dict[str, Any] = {}

    def rec(prefix: str, obj: Any):
        if isinstance(obj, dict):
            # include the dict itself at this prefix (for fields like terms.rounding.delivery)
            if prefix:
                flat[prefix] = obj
            for k, v in obj.items():
                nk = f"{prefix}.{k}" if prefix else k
                rec(nk, v)
        else:
            flat[prefix] = obj

    rec("", d)
    return {k: v for k, v in flat.items() if k}


def _assign_dotted(target: Dict[str, Any], dotted_key: str, value: Any) -> None:
    parts = dotted_key.split(".")
    cur = target
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value


def _merge_llm_on_nulls(rule_result: Dict[str, Any], llm_obj: llm_mod.CSAExtraction) -> Dict[str, Any]:
    """Overlay LLM values only for fields that are null/empty in rule result."""
    final = json.loads(json.dumps(rule_result))  # deep copy
    llm_flat = _flatten_result_keys(llm_obj.dict())
    rule_flat = _flatten_result_keys(rule_result)

    for key, llm_val in llm_flat.items():
        # Skip keys not in our output model
        if key not in rule_flat:
            continue
        rule_val = rule_flat[key]
        should_fill = rule_val in (None, [], {})
        if should_fill and llm_val not in (None, [], {}):
            _assign_dotted(final, key, llm_val)
    return final


def extract_hybrid_from_path(path: str) -> Dict[str, Any]:
    html_bytes = Path(path).read_bytes()
    html_str = html_bytes.decode("utf-8", errors="ignore")
    return extract_hybrid(html_str)


def extract_hybrid(html_content: str) -> Dict[str, Any]:
    """Hybrid extractor: rule-based first, then LLM for null fields, merged."""
    # 1) Rule-based (regex/bs4) using existing parser
    rule_res: Dict[str, Any] = rule_parse_html(html_content)

    # 2) Determine which LLM fields to backfill
    # Use the LLM module's FIELDS list, but only request those currently null/empty
    flat_rule = _flatten_result_keys(rule_res)
    missing_fields: List[str] = []
    for field in llm_mod.FIELDS:
        if field in flat_rule:
            v = flat_rule[field]
            if v in (None, [], {}):
                missing_fields.append(field)
        else:
            # If our rule result doesn't expose this dotted key directly, ask LLM
            missing_fields.append(field)

    # If nothing is missing, return rule result directly
    if not missing_fields:
        return rule_res

    # 3) LLM fallback on only missing fields
    try:
        llm_part = asyncio.run(llm_mod.extract_csa_fields(html_content, missing_fields))
    except Exception as e:
        logger.warning("LLM fallback failed: %s", e)
        return rule_res

    # 4) Merge: fill only null/empty fields with LLM output
    final = _merge_llm_on_nulls(rule_res, llm_part)

    # 5) Targeted backfill (rounding, MTA currency, regime.default) from full LLM if still missing
    targeted = [
        "terms.rounding.delivery",
        "terms.rounding.return",
        "terms.mta.currency",
        "csa.regime.default",
    ]
    need_full = False
    flat_final = _flatten_result_keys(final)
    for k in targeted:
        if k in flat_final and flat_final[k] in (None, [], {}):
            need_full = True
            break
    if need_full:
        try:
            full_llm = asyncio.run(llm_mod.extract_csa(html_content))
            full_flat = _flatten_result_keys(full_llm.dict())
            for k in targeted:
                if k in flat_final and flat_final[k] in (None, [], {}):
                    v = full_flat.get(k)
                    if v not in (None, [], {}):
                        _assign_dotted(final, k, v)
        except Exception as e:
            logger.warning("Full LLM backfill failed: %s", e)

    # 6) Last-mile: explicitly query rounding.delivery if still missing
    flat_final = _flatten_result_keys(final)
    if flat_final.get("terms.rounding.delivery") in (None, [], {}):
        try:
            r_fields = asyncio.run(llm_mod.extract_csa_fields(html_content, ["terms.rounding.delivery"]))
            r_flat = _flatten_result_keys(r_fields.dict())
            rv = r_flat.get("terms.rounding.delivery")
            if rv not in (None, [], {}):
                _assign_dotted(final, "terms.rounding.delivery", rv)
        except Exception as e:
            logger.warning("Rounding.delivery field query failed: %s", e)
    # Attach LLM panel for introspection under 'llm'
    try:
        final_with_llm = {**final, "llm": llm_part.dict()}
    except Exception:
        final_with_llm = final
    return final_with_llm


