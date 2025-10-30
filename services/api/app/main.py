from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .extractor_adapter import ExtractorAdapter, AdapterResult

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))

app = FastAPI(title="CSA Extraction Pro API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


_adapter = ExtractorAdapter()


@app.get("/healthz")
async def healthz() -> Dict[str, Any]:
    return {"status": "ok", "mock": _adapter.mock_mode}


@app.get("/api/data/list")
async def list_data() -> Dict[str, Any]:
    exts = {".htm", ".html", ".pdf", ".docx", ".txt"}
    files: List[str] = []
    if DATA_DIR.exists():
        for p in sorted(DATA_DIR.iterdir()):
            if p.is_file() and p.suffix.lower() in exts:
                files.append(p.name)
    return {"files": files}


@app.post("/api/csa/ingest")
async def ingest(
    file: Optional[UploadFile] = File(default=None),
    path_in_data: Optional[str] = Form(default=None),
    url: Optional[str] = Form(default=None),
) -> Dict[str, Any]:
    tmp_dir = Path("/tmp/csa_ingest")
    tmp_dir.mkdir(parents=True, exist_ok=True)

    if file is not None:
        dest = tmp_dir / f"upload_{uuid.uuid4().hex}_{file.filename}"
        content = await file.read()
        dest.write_bytes(content)
        return {"path": str(dest), "source": "upload"}

    if path_in_data:
        src = DATA_DIR / path_in_data
        if not src.exists():
            raise HTTPException(status_code=404, detail="path_in_data not found")
        dest = tmp_dir / f"data_{uuid.uuid4().hex}_{src.name}"
        try:
            dest.symlink_to(src)
        except Exception:
            dest.write_bytes(src.read_bytes())
        return {"path": str(dest), "source": "data"}

    if url:
        dest = tmp_dir / f"url_{uuid.uuid4().hex}.txt"
        dest.write_text(url)
        return {"path": str(dest), "source": "url"}

    raise HTTPException(status_code=400, detail="Provide file, path_in_data, or url")


@app.post("/api/extract")
async def extract(
    path: str = Form(...),
    with_llm: bool = Form(False),
    model_profile: Optional[str] = Form(default=None),
) -> JSONResponse:
    try:
        result: AdapterResult = _adapter.run(path=path, with_llm=with_llm, model_profile=model_profile)
        return JSONResponse(result.model_dump())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/run/{run_id}/result")
async def get_run(run_id: str) -> Dict[str, Any]:
    return {"run_id": run_id, "status": "mock", "result": {}}


@app.post("/api/run/{run_id}/edit")
async def edit_run(run_id: str, patches: str = Form(...), reason: str = Form(...)) -> Dict[str, Any]:
    return {"run_id": run_id, "applied": True}


@app.post("/api/run/{run_id}/submit")
async def submit_run(run_id: str) -> Dict[str, Any]:
    return {"run_id": run_id, "submitted": True}


@app.post("/api/run/{run_id}/approve")
async def approve_run(run_id: str) -> Dict[str, Any]:
    return {"run_id": run_id, "approved": True}


@app.get("/api/run/{run_id}/versions")
async def run_versions(run_id: str) -> Dict[str, Any]:
    return {"run_id": run_id, "versions": []}


@app.get("/api/run/{run_id}/version/{vid}")
async def run_version(run_id: str, vid: str) -> Dict[str, Any]:
    return {"run_id": run_id, "version": vid, "result": {}}


@app.post("/api/run/{run_id}/commit")
async def commit_run(run_id: str, source: str = Form(...)) -> Dict[str, Any]:
    return {"run_id": run_id, "committed_from": source}
