from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class AdapterResult:
    rule_json: Dict[str, Any]
    llm_json: Optional[Dict[str, Any]]
    diff_json: Dict[str, Any]
    haircuts: Dict[str, Any]
    evidence: Dict[str, Any]
    benchmark: Dict[str, Any]
    run: Dict[str, Any]

    def model_dump(self) -> Dict[str, Any]:
        return asdict(self)


class ExtractorAdapter:
    def __init__(self) -> None:
        self.mock_mode = False
        self._maybe_setup_path()

    def _maybe_setup_path(self) -> None:
        repo_dir = os.getenv("EXTRACTOR_REPO_DIR")
        if repo_dir and repo_dir not in sys.path:
            sys.path.insert(0, repo_dir)

    def _call_python_module(self, path: str, with_llm: bool) -> Optional[Dict[str, Any]]:
        try:
            from extractor.core import extract_csa  # type: ignore
            result = extract_csa(Path(path))
            if isinstance(result, dict):
                return result
        except Exception:
            return None
        return None

    def _call_cli(self, path: str, with_llm: bool) -> Optional[Dict[str, Any]]:
        cli_path = Path(os.getenv("EXTRACTOR_CLI_PATH", "cli.py"))
        if not cli_path.exists():
            return None
        cmd = [sys.executable, str(cli_path), path]
        if with_llm:
            cmd.append("--with-llm")
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            text = proc.stdout.decode("utf-8", errors="ignore")
            return json.loads(text)
        except Exception:
            return None

    def run(self, path: str, with_llm: bool = False, model_profile: Optional[str] = None) -> AdapterResult:
        rule_json: Optional[Dict[str, Any]] = self._call_python_module(path, with_llm)
        if rule_json is None:
            rule_json = self._call_cli(path, with_llm)

        if rule_json is None:
            self.mock_mode = True
            rule_json = {
                "csa.meta.governing_law": "NY",
                "terms.base_currency": "USD",
                "haircuts": {"matrix": []},
            }
            llm_json: Optional[Dict[str, Any]] = {"terms.base_currency": "USD", "llm": True} if with_llm else None
        else:
            self.mock_mode = False
            llm_json = rule_json.get("llm") if isinstance(rule_json, dict) else None

        haircuts = {"matrix": rule_json.get("haircuts", {}).get("matrix", [])} if isinstance(rule_json, dict) else {"matrix": []}

        diff_json: Dict[str, Any] = {}
        if llm_json and isinstance(rule_json, dict):
            keys = sorted(set(rule_json.keys()) | set(llm_json.keys()))
            diff_json = {"keys": keys}

        evidence: Dict[str, Any] = {}
        benchmark: Dict[str, Any] = {"abstains": sum(1 for v in (rule_json.values() if isinstance(rule_json, dict) else []) if v in (None, [], ""))}
        run_meta: Dict[str, Any] = {
            "extractor_version": "local",
            "with_llm": with_llm,
            "model_profile": model_profile,
            "path": path,
        }

        return AdapterResult(
            rule_json=rule_json if isinstance(rule_json, dict) else {},
            llm_json=llm_json if isinstance(llm_json, dict) else None,
            diff_json=diff_json,
            haircuts=haircuts,
            evidence=evidence,
            benchmark=benchmark,
            run=run_meta,
        )
