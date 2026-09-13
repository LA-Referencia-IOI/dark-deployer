"""The local server: confinement, token gate, sessions and saving."""

import json

from fastapi.testclient import TestClient

from webwizard.server import create_app

TOKEN = "test-session-token"


def _client() -> TestClient:
    return TestClient(create_app(TOKEN))


def _unlocked() -> TestClient:
    client = _client()
    assert client.get("/", params={"token": TOKEN}).status_code == 200
    return client


def test_root_without_token_is_forbidden():
    assert _client().get("/").status_code == 403


def test_root_with_token_serves_the_page_and_sets_cookie():
    response = _client().get("/", params={"token": TOKEN})
    assert response.status_code == 200
    assert "web-wizard" in response.text
    assert "wizard_token" in response.headers.get("set-cookie", "")


def test_api_requires_the_session_token():
    assert _client().get("/api/inventories").status_code == 403


def test_inventories_and_graph_flow():
    client = _unlocked()

    listing = client.get("/api/inventories")
    assert listing.status_code == 200
    names = {item["name"] for item in listing.json()["inventories"]}
    assert "local-ha.json" in names

    path = next(
        item["path"] for item in listing.json()["inventories"] if item["name"] == "local-ha.json"
    )
    graph = client.get("/api/graph", params={"path": path})
    assert graph.status_code == 200
    payload = graph.json()
    assert payload["deployment_id"] == "dark-operator-local-ha"
    assert payload["machines"][0]["id"] == "local"


def test_graph_reports_unreadable_inventory_as_400():
    client = _unlocked()
    response = client.get("/api/graph", params={"path": "/nope/does-not-exist.json"})
    assert response.status_code == 400
    assert "not found" in response.json()["detail"]


def test_mutations_are_rejected():
    client = _unlocked()
    assert client.post("/api/graph").status_code == 405


def test_static_assets_are_served_without_cdn():
    client = _unlocked()
    for name in ("index.html", "canvas.js", "style.css"):
        response = client.get(f"/static/{name}")
        assert response.status_code == 200
        assert len(response.content) > 0
    assert client.get("/static/canvas.js").headers["cache-control"] == "no-store"


# -- draft sessions ---------------------------------------------------

def _open(client: TestClient, name: str = "production-six-host.json") -> dict:
    listing = client.get("/api/inventories").json()["inventories"]
    path = next(item["path"] for item in listing if item["name"] == name)
    response = client.post("/api/session", json={"path": path})
    assert response.status_code == 200, response.text
    return response.json()


def test_operations_catalogue_is_exposed():
    catalogue = _unlocked().get("/api/operations").json()["operations"]
    assert "move_group" in catalogue
    assert "set_validator_count" in catalogue


def test_session_lifecycle():
    client = _unlocked()
    state = _open(client)
    assert state["draft"]["changed"] is False
    assert state["draft"]["written"] is False
    session_id = state["session_id"]

    moved = client.post(
        f"/api/session/{session_id}/op",
        json={"kind": "move_group", "params": {"group": "blockchain-b", "machine": "apps"}},
    ).json()
    assert moved["ok"] is True
    assert moved["draft"]["changed"] is True
    assert moved["draft"]["sections"] == ["placement"]
    assert moved["draft"]["can_undo"] is True
    fate = {machine["id"]: machine["if_lost"] for machine in moved["graph"]["machines"]}
    assert fate["apps"]["consensus"] == "quorum_lost"

    undone = client.post(f"/api/session/{session_id}/undo").json()
    assert undone["ok"] is True
    assert undone["draft"]["changed"] is False
    assert undone["draft"]["can_undo"] is False


def test_rejected_operation_reports_and_keeps_the_draft():
    client = _unlocked()
    session_id = _open(client)["session_id"]
    before = client.get(f"/api/session/{session_id}").json()

    response = client.post(
        f"/api/session/{session_id}/op",
        json={"kind": "remove_machine", "params": {"id": "apps"}},
    )
    # A rejected change is a domain answer, not a transport failure.
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "placement.apps" in body["error"]

    after = client.get(f"/api/session/{session_id}").json()
    assert after["graph"] == before["graph"]


def test_reset_clears_the_draft():
    client = _unlocked()
    session_id = _open(client)["session_id"]
    client.post(
        f"/api/session/{session_id}/op",
        json={"kind": "set_profile", "params": {"profile": "lab"}},
    )
    reset = client.post(f"/api/session/{session_id}/reset").json()
    assert reset["draft"]["changed"] is False
    assert reset["draft"]["can_undo"] is False


def test_unknown_session_is_not_found():
    client = _unlocked()
    assert client.get("/api/session/nope").status_code == 404
    assert client.post("/api/session/nope/op", json={"kind": "move_group"}).status_code == 404


def test_session_endpoints_require_the_token():
    assert _client().post("/api/session", json={"path": "x"}).status_code == 403


def test_session_rejects_a_document_that_is_not_a_compact_inventory(tmp_path):
    client = _unlocked()
    other = tmp_path / "v3.json"
    other.write_text('{"version": 3}')
    response = client.post("/api/session", json={"path": str(other)})
    assert response.status_code == 400
    assert "compact" in response.json()["detail"]


# -- saving -----------------------------------------------------------

def test_saving_creates_a_new_file(tmp_path):
    client = _unlocked()
    session_id = _open(client)["session_id"]
    client.post(
        f"/api/session/{session_id}/op",
        json={"kind": "set_profile", "params": {"profile": "lab"}},
    )

    target = tmp_path / "saved.json"
    body = client.post(f"/api/session/{session_id}/save", json={"path": str(target)}).json()

    assert body["ok"] is True
    assert body["saved"] == str(target)
    assert body["draft"]["saved_path"] == str(target)
    assert json.loads(target.read_text(encoding="utf-8"))["profile"] == "lab"


def test_saving_twice_to_the_same_path_is_refused(tmp_path):
    client = _unlocked()
    session_id = _open(client)["session_id"]
    target = tmp_path / "saved.json"

    assert client.post(f"/api/session/{session_id}/save", json={"path": str(target)}).json()["ok"] is True
    second = client.post(f"/api/session/{session_id}/save", json={"path": str(target)}).json()

    assert second["ok"] is False
    assert "refusing to overwrite" in second["error"]


def test_save_endpoint_requires_the_token(tmp_path):
    client = _client()
    response = client.post("/api/session/anything/save", json={"path": str(tmp_path / "x.json")})
    assert response.status_code == 403
