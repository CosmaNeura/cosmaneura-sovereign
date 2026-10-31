"""Generated delivery is byte-bound to readable source; original delivery remains."""
import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


def test_generated_delivery_manifest_and_normal_script_route():
    root = Path(__file__).parents[1]
    manifest = json.loads((root / "app/panel.delivery.json").read_text())
    source = (root / manifest["source_path"]).read_bytes()
    delivery = (root / manifest["delivery_path"]).read_bytes()
    assert hashlib.sha256(source).hexdigest() == manifest["source_sha256"]
    assert hashlib.sha256(delivery).hexdigest() == manifest["delivery_sha256"]
    assert manifest["compiler"] == "esbuild" and manifest["compiler_version"] == "0.25.12"
    assert manifest["options"] == {
        "loader": "js", "minify": True, "target": "es2020", "charset": "utf8",
        "legalComments": "none", "sourcemap": False,
    }
    client = TestClient(app)
    result = client.get("/embed/v1/panel.delivery.js")
    assert result.status_code == 200 and result.content == delivery
    assert "javascript" in result.headers["content-type"]
    assert client.get("/embed/v1/panel.js").content == client.get("/panel.js").content == source
