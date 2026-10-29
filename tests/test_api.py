import pytest
from fastapi.testclient import TestClient
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "api.sqlite3", step_delay=0)) as item:
        yield item


def login(client, role="analyst"):
    response = client.post("/api/session", json={"role": role})
    assert response.status_code == 200


def test_api_requires_demo_session(client):
    assert client.get("/api/state").status_code == 401
    assert client.get("/healthz").json()["status"] == "ok"
    login(client)
    state = client.get("/api/state").json()
    assert len(state["incidents"]) == 5
    assert "fixture" not in state["incidents"][0]
    assert state["mode"] == "simulated"


def test_api_complete_journey_and_roles(client):
    login(client)
    case = client.get("/api/state").json()["incidents"][0]
    assert client.post(f"/api/incidents/{case['id']}/investigate", json={"version": case["version"]}).status_code == 200
    case = client.get(f"/api/incidents/{case['id']}").json()
    assert case["status"] == "pending_review"
    response = client.post(f"/api/incidents/{case['id']}/review", json={"version": case["version"], "verdict": "malicious", "comment": "已核验模拟请求与应用日志"})
    assert response.status_code == 200
    case = response.json()
    assert client.post(f"/api/incidents/{case['id']}/approve", json={"version": case["version"], "approved": True, "comment": "同意"}).status_code == 403
    login(client, "manager")
    case = client.post(f"/api/incidents/{case['id']}/approve", json={"version": case["version"], "approved": True, "comment": "同意临时限制"}).json()
    login(client, "operator")
    case = client.post(f"/api/incidents/{case['id']}/execute", json={"version": case["version"]}).json()
    assert case["status"] == "awaiting_verification"
    login(client)
    case = client.post(f"/api/incidents/{case['id']}/verify", json={"version": case["version"]}).json()
    assert case["status"] == "resolved"
    assert "模拟" in client.get(f"/api/incidents/{case['id']}/report").text


def test_origin_and_input_validation(client):
    assert client.post("/api/session", json={"role": "manager"}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/session", json={"role": "root"}).status_code == 422
    login(client)
    assert client.post("/api/incidents", json={"scenario": "<script>"}).status_code == 422
    assert client.get("/api/incidents/no-such-id").status_code == 404
    assert client.get("/").headers["content-security-policy"].startswith("default-src 'self'")


def test_operator_cannot_start_investigation(client):
    login(client, "operator")
    case = client.get("/api/state").json()["incidents"][0]
    assert client.post(f"/api/incidents/{case['id']}/investigate", json={"version": case["version"]}).status_code == 403


def test_unknown_mutation_returns_404(client):
    login(client)
    assert client.post("/api/incidents/not-found/investigate", json={"version": 0}).status_code == 404
