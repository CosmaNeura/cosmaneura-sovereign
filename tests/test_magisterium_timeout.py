"""Magisterium may wait longer for review without widening other I/O budgets."""
from __future__ import annotations

import asyncio

import httpx
import pytest
from pydantic import ValidationError

from app.clients.magisterium import MagisteriumClient
from app.config import Settings


@pytest.mark.parametrize("read_seconds,other_seconds", [(None, 30.0), (75.0, 8.0)])
def test_actual_magisterium_request_uses_separate_read_budget(monkeypatch, read_seconds, other_seconds):
    overrides = {} if read_seconds is None else {"magisterium_read_timeout_seconds": read_seconds}
    settings = Settings(
        _env_file=None, mock_mode=False, magisterium_api_key="synthetic-key",
        magisterium_base_url="https://magisterium.example.invalid",
        request_timeout_seconds=other_seconds, **overrides,
    )
    calls = []

    def handler(request):
        calls.append(request.extensions["timeout"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "Synthetic review"}}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(handler), **kwargs,
    ))
    monkeypatch.setattr("app.clients.magisterium.assert_safe_outbound_url", lambda _url: None)
    answer, citations = asyncio.run(MagisteriumClient(settings).ask(
        system="Synthetic", user_content="Fictional ethics question",
    ))
    assert answer == "Synthetic review" and citations == []
    assert calls == [{"connect": other_seconds, "read": 90.0 if read_seconds is None else read_seconds,
                      "write": other_seconds, "pool": other_seconds}]
    assert settings.request_timeout_seconds == other_seconds


def test_magisterium_read_budget_accepts_explicit_environment_override(monkeypatch):
    monkeypatch.setenv("MAGISTERIUM_READ_TIMEOUT_SECONDS", "60")
    settings = Settings(_env_file=None)
    assert settings.magisterium_read_timeout_seconds == 60.0
    assert settings.request_timeout_seconds == 30.0


@pytest.mark.parametrize("seconds", [1.0, 120.0])
def test_magisterium_read_budget_accepts_finite_boundaries(seconds):
    assert Settings(_env_file=None, magisterium_read_timeout_seconds=seconds).magisterium_read_timeout_seconds == seconds


@pytest.mark.parametrize("seconds", [-1.0, 0.0, 0.99, 120.01, float("inf"), float("-inf"), float("nan")])
def test_magisterium_read_budget_rejects_invalid_configuration(seconds):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, magisterium_read_timeout_seconds=seconds)
