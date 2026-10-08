"""Application factory, error responses, request ids and JSON logs."""

import json
import logging
import sys

import pytest
from fastapi.testclient import TestClient

from app.config import ConfigError
from app.logging_config import JsonFormatter
from app.main import create_app
from tests.conftest import make_settings

UNREACHABLE = "postgresql+psycopg://u:p@127.0.0.1:1/d"


def test_create_app_without_database_url_fails_fast() -> None:
    with pytest.raises(ConfigError, match="DATABASE_URL is not set"):
        create_app()


def test_create_app_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE)
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    app = create_app()
    assert app.state.settings.log_level == "warning"
    assert logging.getLogger().level == logging.WARNING


def test_create_app_opens_no_connection() -> None:
    # Startup must not need the database: the engine connects on first use only.
    app = create_app(make_settings(UNREACHABLE))
    with TestClient(app):
        assert app.state.engine.pool.checkedout() == 0


def test_unknown_route_is_404_json() -> None:
    with TestClient(create_app(make_settings(UNREACHABLE))) as client:
        for path in ("/nope", "/api", "/api/health/extra", "/api/%E2%9C%93", "/" + "a" * 5000):
            response = client.get(path)
            assert response.status_code == 404, path
            assert response.headers["content-type"] == "application/json"
            assert response.json() == {"detail": "Not Found"}


def test_docs_served_outside_production() -> None:
    with TestClient(create_app(make_settings(UNREACHABLE))) as client:
        assert client.get("/docs").status_code == 200
        schema = client.get("/openapi.json").json()
        responses = schema["paths"]["/api/health"]["get"]["responses"]
        assert set(responses) == {"200", "503"}
        assert responses["503"]["content"]["application/json"]["schema"]["$ref"].endswith(
            "/HealthDown"
        )


def test_docs_hidden_in_production() -> None:
    settings = make_settings(UNREACHABLE).model_copy(
        update={"app_env": "production", "secret_key": None}
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404


def test_request_id_generated_and_echoed() -> None:
    with TestClient(create_app(make_settings(UNREACHABLE))) as client:
        generated = client.get("/nope").headers["x-request-id"]
        assert len(generated) == 32
        assert client.get("/nope", headers={"X-Request-ID": "abc"}).headers["x-request-id"] == "abc"
        long_id = client.get("/nope", headers={"X-Request-ID": "x" * 500}).headers["x-request-id"]
        assert long_id == "x" * 64


def test_unhandled_error_is_500_json() -> None:
    app = create_app(make_settings(UNREACHABLE))

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("kaboom")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom")
    assert response.status_code == 500
    assert response.json() == {"detail": "internal server error"}
    assert "kaboom" not in response.text


def test_logs_are_one_json_object_per_line(capsys: pytest.CaptureFixture[str]) -> None:
    with TestClient(create_app(make_settings(UNREACHABLE))) as client:
        client.get("/nope", headers={"X-Request-ID": "trace-1"})
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    request_logs = [line for line in lines if line["msg"] == "request"]
    assert request_logs, lines
    entry = request_logs[0]
    assert entry["request_id"] == "trace-1"
    assert entry["path"] == "/nope"
    assert entry["status"] == 404
    assert entry["level"] == "info"
    assert isinstance(entry["duration_ms"], float)
    starting = next(line for line in lines if line["msg"] == "api starting")
    assert starting["database"] == "postgresql+psycopg://u:***@127.0.0.1:1/d"


def test_json_formatter_handles_unicode_and_exceptions() -> None:
    try:
        raise ValueError("bad")
    except ValueError:
        record = logging.getLogger("t").makeRecord(
            "t", logging.ERROR, __file__, 1, "café ✓ %s", ("ok",), exc_info=sys.exc_info()
        )
    record.custom = {"a": 1}
    payload = json.loads(JsonFormatter().format(record))
    assert payload["msg"] == "café ✓ ok"
    assert payload["custom"] == {"a": 1}
    assert "ValueError: bad" in payload["exc"]
    assert payload["level"] == "error"
