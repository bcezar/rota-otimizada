from uuid import uuid4

import pytest

from app.routers import routes


@pytest.fixture(autouse=True)
def isolate_anonymous_usage_between_tests(monkeypatch):
    """Keep anonymous optimization quotas isolated for each test case."""
    anonymous_id = f"pytest-{uuid4().hex}"
    monkeypatch.setattr(routes, "_anonymous_id", lambda _request: anonymous_id)
