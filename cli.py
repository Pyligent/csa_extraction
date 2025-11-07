import click
import json
from pathlib import Path
from datetime import datetime
from copy import deepcopy
from typing import Any, Dict, Tuple

from extractor.core import extract_csa
import asyncio
from extractor.parsers.csa_llm_extraction import extract_csa as llm_extract_csa, extract_csa_fields as llm_extract_csa_fields
from extractor.parsers.csa_llm_extraction import _fallback_parse_rounding as llm_fallback_rounding  # type: ignore
from extractor.csa_extractor_hybrid import extract_hybrid_from_path
from extractor.csa_hybridv2_extractor import extract_hybridv2_from_path


ResultDict = Dict[str, Any]
PRESERVE_NESTED_KEYS = {"paragraph_13"}


def _is_nullish(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (list, tuple, set, dict)) and not value:
        return True
    return False


def _flatten_dict(data: Dict[str, Any], prefix: str) -> Dict[str, Any]:
    flat: Dict[str, Any] = {}
    for key, value in data.items():
        new_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(_flatten_dict(value, new_key))
        else:
            flat[new_key] = deepcopy(value)
    return flat


def _split_result_for_merge(data: ResultDict) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    flat: Dict[str, Any] = {}
    preserved: Dict[str, Any] = {}
    if not isinstance(data, dict):
        return flat, preserved

    items = data.items()
    for key, value in items:
        if key in {"llm", "_source", "_confidence"}:
            continue
        if key == "error":
            continue
        if key in PRESERVE_NESTED_KEYS and isinstance(value, dict):
            preserved[key] = deepcopy(value)
            continue
        if isinstance(value, dict):
            flat.update(_flatten_dict(value, key))
        else:
            flat[key] = deepcopy(value)
    return flat, preserved


def _merge_preserved_dict(existing: Any, candidate: Any) -> Any:
    if not isinstance(candidate, dict):
        if existing is None or _is_nullish(existing):
            return deepcopy(candidate)
        return existing

    if existing is None or not isinstance(existing, dict):
        existing = {}

    merged = deepcopy(existing)
    for key, value in candidate.items():
        if isinstance(value, dict):
            merged[key] = _merge_preserved_dict(merged.get(key), value)
        else:
            if key not in merged or _is_nullish(merged[key]):
                merged[key] = deepcopy(value)
    return merged


def _merge_flat_dict(merged: Dict[str, Any], candidate: Dict[str, Any]) -> None:
    for key, value in candidate.items():
        if key not in merged:
            merged[key] = deepcopy(value)
            continue
        if _is_nullish(merged[key]) and not _is_nullish(value):
            merged[key] = deepcopy(value)


def _merge_results(rule: ResultDict, llm: ResultDict, hybrid: ResultDict, v2: ResultDict) -> ResultDict:
    order = [hybrid, v2, llm, rule]
    merged_flat: Dict[str, Any] = {}
    merged_preserved: Dict[str, Any] = {}
    initialized = False

    for payload in order:
        if not isinstance(payload, dict):
            continue
        flat, preserved = _split_result_for_merge(payload)
        if not initialized:
            merged_flat = deepcopy(flat)
            merged_preserved = deepcopy(preserved)
            initialized = True
            continue
        _merge_flat_dict(merged_flat, flat)
        for key, value in preserved.items():
            merged_preserved[key] = _merge_preserved_dict(merged_preserved.get(key), value)

    final: Dict[str, Any] = merged_flat if initialized else {}
    for key, value in merged_preserved.items():
        final[key] = value
    return final


def _compute_diff(rule: ResultDict, llm_res: ResultDict, hyb: ResultDict, v2_res: ResultDict) -> Dict[str, Any]:
    def get(d: ResultDict, dotted: str, default: Any = None):
        cur = d
        ok = True
        for c in dotted.split('.'):
            if not isinstance(cur, dict) or c not in cur:
                ok = False
                break
            cur = cur[c]
        if ok:
            return cur
        return d.get(dotted, default) if isinstance(d, dict) else default

    def haircuts_len(d: ResultDict):
        v = get(d, 'haircuts.matrix')
        return len(v) if isinstance(v, list) else None

    keys = [
        'parties.party_B.name',
        'parties.party_A.role',
        'parties.party_B.role',
        'terms.mta.amount',
        'terms.mta.currency',
        'terms.rounding.delivery',
        'terms.regular_settlement_day',
        'terms.base_currency',
        'csa.regime.default',
    ]
    diff = {
        'haircuts.matrix.length': {
            'rule': haircuts_len(rule),
            'llm': haircuts_len(llm_res),
            'hybrid': haircuts_len(hyb),
            'v2': haircuts_len(v2_res),
        }
    }
    for k in keys:
        diff[k] = {
            'rule': get(rule, k),
            'llm': get(llm_res, k),
            'hybrid': get(hyb, k),
            'v2': get(v2_res, k),
        }
    return diff


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))


def _resolve_path(file_path: str) -> Path:
    p = Path(file_path)
    if p.exists():
        return p
    if '@' in p.name:
        alt = p.with_name(p.name.replace('@', ''))
        if alt.exists():
            return alt
    return p


def _run_full_suite(file_path: str, results_root: str) -> None:
    target_path = _resolve_path(file_path)
    if not target_path.exists():
        click.echo(json.dumps({"error": f"File not found: {file_path}"}, indent=2, ensure_ascii=False))
        return

    data_name = target_path.stem.lstrip('@')
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = Path(results_root)
    output_dir = root / f"{data_name}_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    errors: Dict[str, str] = {}

    # Rule-based
    try:
        rule = extract_csa(str(target_path))
    except Exception as e:
        rule = {"error": str(e)}
        errors["non_llm"] = str(e)

    _write_json(output_dir / f"{data_name}_non_llm.json", rule)
    click.echo(f"Saved non-LLM result to {(output_dir / f'{data_name}_non_llm.json').as_posix()}")

    # LLM
    try:
        html_bytes = target_path.read_bytes()
        html_str = html_bytes.decode("utf-8", errors="ignore")
        llm_obj = asyncio.run(llm_extract_csa(html_str))
        llm_res: ResultDict = llm_obj.dict()
    except Exception as e:
        llm_res = {"error": str(e)}
        errors["llm"] = str(e)

    _write_json(output_dir / f"{data_name}_llm.json", llm_res)
    click.echo(f"Saved LLM result to {(output_dir / f'{data_name}_llm.json').as_posix()}")

    # Hybrid
    try:
        hyb = extract_hybrid_from_path(str(target_path))
    except Exception as e:
        hyb = {"error": str(e)}
        errors["hybird"] = str(e)

    _write_json(output_dir / f"{data_name}_hybird.json", hyb)
    click.echo(f"Saved hybrid result to {(output_dir / f'{data_name}_hybird.json').as_posix()}")

    # Hybrid v2
    try:
        v2 = extract_hybridv2_from_path(str(target_path))
    except Exception as e:
        v2 = {"error": str(e)}
        errors["v2"] = str(e)

    _write_json(output_dir / f"{data_name}_v2.json", v2)
    click.echo(f"Saved hybrid v2 result to {(output_dir / f'{data_name}_v2.json').as_posix()}")

    diff_report = _compute_diff(rule if isinstance(rule, dict) else {}, llm_res, hyb if isinstance(hyb, dict) else {}, v2 if isinstance(v2, dict) else {})
    diff_path = output_dir / f"{data_name}_diff.json"
    _write_json(diff_path, diff_report)
    click.echo(f"Saved diff report to {diff_path.as_posix()}")

    merged = _merge_results(rule if isinstance(rule, dict) else {}, llm_res, hyb if isinstance(hyb, dict) else {}, v2 if isinstance(v2, dict) else {})
    merge_path = output_dir / f"{data_name}_merge.json"
    _write_json(merge_path, merged)
    click.echo(f"Saved merged result to {merge_path.as_posix()}")

    if errors:
        errors_path = output_dir / f"{data_name}_errors.json"
        _write_json(errors_path, errors)
        click.echo(f"Recorded extraction errors to {errors_path.as_posix()}")


@click.command()
@click.argument("file_path")
@click.option("--output", "-o", help="Output JSON file")
@click.option("--with-llm/--no-llm", default=False, help="Also run LLM extractor and add under 'llm' key")
@click.option("--llm-fields", default="", help="Comma-separated subset of fields to extract with LLM (optional)")
@click.option("--hybrid/--no-hybrid", default=False, help="Run hybrid (rule-based + LLM fallback) and output merged result")
@click.option("--hybridv2/--no-hybridv2", default=False, help="Run hybrid v2 (independent pipeline) and output merged result")
@click.option("--compare", is_flag=True, help="Print diffs of key fields between rule, llm, and hybrid")
@click.option("--all", is_flag=True, help="Run non-llm, llm, hybrid, hybridv2 and then compare")
@click.option("--suite", "full_suite", is_flag=True, help="Run full extractor suite, save outputs in timestamped folder, and merge results")
@click.option("--suite-results-root", default="tests/results", help="Directory to store suite outputs (default: tests/results)")
def cli(file_path, output, with_llm, llm_fields, hybrid, hybridv2, compare, all, full_suite, suite_results_root):
    if full_suite:
        _run_full_suite(file_path, suite_results_root)
        return

    # All-in-one: generate all outputs, then compare
    if all:
        p = Path(file_path)
        if not p.exists():
            click.echo(json.dumps({"error": f"File not found: {file_path}"}, indent=2, ensure_ascii=False))
            return
        # Rule
        try:
            rule = extract_csa(file_path)
        except Exception as e:
            rule = {"error": str(e)}
        out_rule = p.with_name(f"{p.stem}_non_llm.json").name
        Path(out_rule).write_text(json.dumps(rule, indent=2, ensure_ascii=False))
        click.echo(f"Saved to {out_rule}")
        # LLM
        try:
            html_bytes = p.read_bytes()
            html_str = html_bytes.decode("utf-8", errors="ignore")
            llm_obj = asyncio.run(llm_extract_csa(html_str))
            llm_res = llm_obj.dict()
        except Exception as e:
            llm_res = {"error": str(e)}
        out_llm = p.with_name(f"{p.stem}_llm.json").name
        Path(out_llm).write_text(json.dumps(llm_res, indent=2, ensure_ascii=False))
        click.echo(f"Saved to {out_llm}")
        # Hybrid
        try:
            hyb = extract_hybrid_from_path(file_path)
        except Exception as e:
            hyb = {"error": str(e)}
        out_hyb = p.with_name(f"{p.stem}_hybird.json").name
        Path(out_hyb).write_text(json.dumps(hyb, indent=2, ensure_ascii=False))
        click.echo(f"Saved to {out_hyb}")
        # Hybrid v2
        try:
            v2 = extract_hybridv2_from_path(file_path)
        except Exception as e:
            v2 = {"error": str(e)}
        out_v2 = p.with_name(f"{p.stem}_v2.json").name
        Path(out_v2).write_text(json.dumps(v2, indent=2, ensure_ascii=False))
        click.echo(f"Saved to {out_v2}")

        # Compare summary
        diff = _compute_diff(rule, llm_res, hyb, v2)
        click.echo(json.dumps(diff, indent=2, ensure_ascii=False))
        return
    # Compare mode: run rule, llm, and hybrid; print concise diff JSON
    if compare:
        path = Path(file_path)
        if not path.exists():
            click.echo(json.dumps({"error": f"File not found: {file_path}"}, indent=2, ensure_ascii=False))
            return
        html_bytes = path.read_bytes()
        html_str = html_bytes.decode("utf-8", errors="ignore")
        rule = extract_csa(file_path)
        try:
            llm_obj = asyncio.run(llm_extract_csa(html_str))
            llm_res = llm_obj.dict()
            # Guardrail: if LLM haircuts empty, backfill from rule
            try:
                if isinstance(llm_res, dict) and not (llm_res.get('haircuts', {}).get('matrix')):
                    if isinstance(rule, dict) and rule.get('haircuts', {}).get('matrix'):
                        llm_res.setdefault('haircuts', {})['matrix'] = rule['haircuts']['matrix']
            except Exception:
                pass
            # Guardrail: if LLM rounding.delivery missing, try regex fallback on raw html
            try:
                rdel = llm_res.get('terms', {}).get('rounding', {}).get('delivery') if isinstance(llm_res.get('terms'), dict) else None
                if not rdel:
                    d, r = llm_fallback_rounding(html_str)
                    if d:
                        llm_res.setdefault('terms', {}).setdefault('rounding', {})['delivery'] = d
                    if r and not llm_res.get('terms', {}).get('rounding', {}).get('return'):
                        llm_res.setdefault('terms', {}).setdefault('rounding', {})['return'] = r
            except Exception:
                pass
        except Exception as e:
            llm_res = {"error": str(e)}
        try:
            hyb = extract_hybrid_from_path(file_path)
        except Exception as e:
            hyb = {"error": str(e)}
        try:
            v2 = extract_hybridv2_from_path(file_path)
        except Exception as e:
            v2 = {"error": str(e)}

        diff = _compute_diff(rule, llm_res, hyb, v2)
        click.echo(json.dumps(diff, indent=2, ensure_ascii=False))
        return
    # Hybrid path: preferred when requested
    if hybrid:
        try:
            final = extract_hybrid_from_path(file_path)
        except Exception as e:
            click.echo(json.dumps({"error": str(e)}, indent=2, ensure_ascii=False))
            return
        # Default output name if not provided
        if not output:
            p = Path(file_path)
            output = p.with_name(f"{p.stem}_hybird.json").name
        json_str = json.dumps(final, indent=2, ensure_ascii=False)
        Path(output).write_text(json_str)
        click.echo(f"Saved to {output}")
        return

    # Hybrid v2 path
    if hybridv2:
        try:
            final = extract_hybridv2_from_path(file_path)
        except Exception as e:
            click.echo(json.dumps({"error": str(e)}, indent=2, ensure_ascii=False))
            return
        if not output:
            p = Path(file_path)
            output = p.with_name(f"{p.stem}_v2.json").name
        json_str = json.dumps(final, indent=2, ensure_ascii=False)
        Path(output).write_text(json_str)
        click.echo(f"Saved to {output}")
        return
    try:
        path = Path(file_path)
        if not path.exists():
            result = {"error": f"File not found: {file_path}"}
            llm = None
            if with_llm:
                llm = {"error": "Input file not found"}
            final = result if llm is None else {**result, "llm": llm}
        else:
            # Rule-based extraction with guard
            try:
                result = extract_csa(file_path)
            except Exception as e:
                result = {"error": str(e)}

            # Optionally run LLM extractor when API key is configured
            llm = None
            if with_llm:
                try:
                    html_bytes = path.read_bytes()
                    html_str = html_bytes.decode("utf-8", errors="ignore")
                    if llm_fields.strip():
                        fields = [f.strip() for f in llm_fields.split(",") if f.strip()]
                        llm_obj = asyncio.run(llm_extract_csa_fields(html_str, fields))
                    else:
                        llm_obj = asyncio.run(llm_extract_csa(html_str))
                    llm = llm_obj.dict()
                except Exception as e:
                    llm = {"error": str(e)}

            final = result if llm is None else {**(result if isinstance(result, dict) else {}), "llm": llm}

        json_str = json.dumps(final, indent=2, ensure_ascii=False)
        # Default output filenames when not provided
        if not output:
            p = Path(file_path)
            if with_llm:
                output = p.with_name(f"{p.stem}_llm.json").name
            else:
                output = p.with_name(f"{p.stem}_non_llm.json").name
        Path(output).write_text(json_str)
        click.echo(f"Saved to {output}")
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    cli()
