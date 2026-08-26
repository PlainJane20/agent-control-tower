import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from audit import log_event, read_events


@pytest.fixture
def audit_path(tmp_path):
    return tmp_path / "audit.jsonl"


def test_log_event_returns_id(audit_path):
    event_id = log_event("agent-a", "llm_call", "test event", path=audit_path)
    assert event_id


def test_read_events_returns_logged_event(audit_path):
    log_event("agent-a", "llm_call", "first call", path=audit_path)
    events = read_events(path=audit_path)
    assert len(events) == 1
    assert events[0]["summary"] == "first call"


def test_read_events_is_append_only_and_ordered(audit_path):
    log_event("agent-a", "llm_call", "first", path=audit_path)
    log_event("agent-a", "llm_call", "second", path=audit_path)
    events = read_events(path=audit_path)
    assert [e["summary"] for e in events] == ["first", "second"]


def test_read_events_filters_by_agent(audit_path):
    log_event("agent-a", "llm_call", "a's call", path=audit_path)
    log_event("agent-b", "llm_call", "b's call", path=audit_path)
    events = read_events(agent_name="agent-a", path=audit_path)
    assert len(events) == 1
    assert events[0]["summary"] == "a's call"


def test_read_events_filters_by_kind(audit_path):
    log_event("agent-a", "llm_call", "a call", path=audit_path)
    log_event("agent-a", "write_action_executed", "a write", path=audit_path)
    events = read_events(kind="write_action_executed", path=audit_path)
    assert len(events) == 1
    assert events[0]["summary"] == "a write"


def test_read_events_empty_when_no_file(tmp_path):
    events = read_events(path=tmp_path / "does_not_exist.jsonl")
    assert events == []
