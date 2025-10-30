import os
import sys
from pathlib import Path

# Point DATA_DIR to a temp folder under tests runtime; pytest will create it if missing
TESTS_DIR = Path(__file__).resolve().parent
TMP_DATA = TESTS_DIR / "_tmp_data"
TMP_DATA.mkdir(exist_ok=True)

os.environ["DATA_DIR"] = str(TMP_DATA)

# Put services/api on sys.path then import app
ROOT = TESTS_DIR.parent
API_DIR = ROOT / "services" / "api"
sys.path.insert(0, str(API_DIR))

from fastapi.testclient import TestClient  # type: ignore
from app.main import app  # type: ignore


client = TestClient(app)


def test_healthz_ok():
    r = client.get("/healthz")
    assert r.status_code == 200
    j = r.json()
    assert j.get("status") == "ok"
    assert "mock" in j


def test_data_list_empty_then_file():
    # initially empty
    r = client.get("/api/data/list")
    assert r.status_code == 200
    assert isinstance(r.json().get("files"), list)

    # add a sample eligible file and expect it to appear
    f = TMP_DATA / "sample.htm"
    f.write_text("<html>ok</html>")
    r2 = client.get("/api/data/list")
    assert "sample.htm" in r2.json().get("files", [])
