from fastapi import FastAPI, File, UploadFile
from extractor.core import extract_csa
import tempfile
import shutil
from pathlib import Path

app = FastAPI(title="Universal CSA Extractor")


@app.post("/extract")
async def extract(file: UploadFile = File(...)):
    suffix = Path(file.filename).suffix.lower()
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name
    try:
        result = extract_csa(tmp_path)
        return result
    finally:
        Path(tmp_path).unlink(missing_ok=True)
