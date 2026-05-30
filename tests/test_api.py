import pytest
from unittest.mock import patch

# All monitor and scan routes require X-Monitor-Key header.
# Use the test key set in conftest.py.
AUTH = {"X-Monitor-Key": "test-monitor-key-for-pytest"}


def test_read_root(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@patch("app.api.routers.monitor.db")
def test_get_stats(mock_db, client):
    # Trigger a DB error so we can test error handling without a real DB.
    mock_db.table.side_effect = Exception("DB Down")
    response = client.get("/monitor/stats", headers=AUTH)
    # Monitor router catches exceptions and returns 500
    assert response.status_code == 500


@patch("app.workers.celery_app.app.send_task")
def test_trigger_scan(mock_send_task, client):
    mock_task = type("obj", (object,), {"id": "task-123"})
    mock_send_task.return_value = mock_task
    payload = {"source": "shodan", "query": "telegram"}
    response = client.post("/scan/trigger", json=payload, headers=AUTH)
    assert response.status_code == 200
    assert response.json()["task_id"] == "task-123"


def test_trigger_scan_invalid_source(client):
    payload = {"source": "invalid", "query": "telegram"}
    response = client.post("/scan/trigger", json=payload, headers=AUTH)
    assert response.status_code == 400
