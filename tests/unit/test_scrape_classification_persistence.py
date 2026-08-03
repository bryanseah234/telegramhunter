from types import SimpleNamespace

import pytest

from app.services._scraper.results import ScrapeReason, ScrapeResult, StrategyAttempt


class _Query:
    def __init__(self, db):
        self.db = db
        self.operation = None
        self.payload = None

    def select(self, *_args, **_kwargs):
        self.operation = "select"
        return self

    def update(self, payload):
        self.operation = "update"
        self.payload = payload
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def single(self):
        return self


class _Db:
    def __init__(self):
        self.queries = []

    def table(self, _name):
        query = _Query(self)
        self.queries.append(query)
        return query


@pytest.mark.asyncio
async def test_persist_scrape_classification_merges_meta_and_audits(monkeypatch):
    from app.workers.tasks import flow_tasks

    fake_db = _Db()
    audit_events = []

    async def fake_async_execute(query):
        if query.operation == "select":
            return SimpleNamespace(data={"meta": {"existing": True}})
        if query.operation == "update":
            return SimpleNamespace(data=[query.payload])
        return SimpleNamespace(data=[])

    monkeypatch.setattr(flow_tasks, "db", fake_db)
    monkeypatch.setattr(flow_tasks, "async_execute", fake_async_execute)
    monkeypatch.setattr(
        flow_tasks.AuditLogger,
        "log",
        lambda *args, **kwargs: audit_events.append((args, kwargs)),
    )

    result = ScrapeResult(
        messages=[],
        reason=ScrapeReason.WEBHOOK_CONFLICT,
        retryable=False,
        evidence={"webhook_url": "https://example.test/hook"},
        strategy_attempts=[
            StrategyAttempt(name="bot_api_updates", reason=ScrapeReason.WEBHOOK_CONFLICT)
        ],
    )

    await flow_tasks._persist_scrape_classification("cred-1", result)

    update_payload = fake_db.queries[-1].payload["meta"]
    assert update_payload["existing"] is True
    assert update_payload["last_scrape_reason"] == "webhook_conflict"
    assert update_payload["last_scrape_retryable"] is False
    assert update_payload["last_scrape_at"]
    assert len(audit_events) == 2
