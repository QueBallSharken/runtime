import shutil
import subprocess

from fastapi.testclient import TestClient

from halcyon_core.api.server import create_app
from halcyon_core.config import RuntimeConfig
from halcyon_core.model.client import LocalModelClient


def fake_client():
    return LocalModelClient(
        "http://local.test/v1",
        "fake",
        transport=lambda url, payload: {"choices": [{"message": {"content": "hud"}}]},
    )


def test_hud_routes_return_html(tmp_path):
    app = create_app(RuntimeConfig(database_path=str(tmp_path / "halcyon.sqlite")), model_client=fake_client())
    client = TestClient(app)

    root = client.get("/")
    hud = client.get("/hud")

    assert root.status_code == 200
    assert hud.status_code == 200
    assert "text/html" in root.headers["content-type"]
    assert "Halcyon" in root.text
    assert "Raw Artifact Viewer" in root.text


def test_hud_html_references_static_assets_and_api_routes(tmp_path):
    app = create_app(RuntimeConfig(database_path=str(tmp_path / "halcyon.sqlite")), model_client=fake_client())
    client = TestClient(app)

    html = client.get("/hud").text
    js = client.get("/static/hud.js").text

    assert "/static/hud.css" in html
    assert "/static/hud.js" in html
    for endpoint in ("/health", "/chat", "/memory/proposals", "/maintenance/pulse", "/maintenance/report"):
        assert endpoint in js
    assert "view raw JSON" in html
    assert "event_index" in js


def test_openapi_still_available(tmp_path):
    app = create_app(RuntimeConfig(database_path=str(tmp_path / "halcyon.sqlite")), model_client=fake_client())
    client = TestClient(app)

    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Halcyon Local API"


def test_no_tool_execution_route_exposed(tmp_path):
    app = create_app(RuntimeConfig(database_path=str(tmp_path / "halcyon.sqlite")), model_client=fake_client())
    client = TestClient(app)

    paths = client.get("/openapi.json").json()["paths"]

    assert all("tool" not in path or "execute" not in path for path in paths)
    assert "/tools/execute" not in paths


def test_hud_javascript_syntax():
    node = shutil.which("node")
    if node is None:
        return
    result = subprocess.run(
        [node, "--check", "halcyon_core/api/static/hud.js"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
