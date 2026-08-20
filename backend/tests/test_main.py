from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def test_healthz() -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_reports_db_connectivity() -> None:
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_response_carries_request_id() -> None:
    response = client.get("/healthz")
    assert "x-request-id" in response.headers


def test_validate_kernel_accepts_valid_segment() -> None:
    response = client.post(
        "/api/v1/kernel/validate",
        json={
            "node": "n1",
            "order_channel": "c1",
            "txn_type": "acquisition",
            "g": ["0.5", "0.3"],
            "breakage": "0.2",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"valid": True, "detail": None}


def test_validate_kernel_rejects_bad_sum() -> None:
    response = client.post(
        "/api/v1/kernel/validate",
        json={
            "node": "n1",
            "order_channel": "c1",
            "txn_type": "acquisition",
            "g": ["0.5", "0.3"],
            "breakage": "0.5",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["detail"] is not None
