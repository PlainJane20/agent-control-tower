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


# --- hash chain ---------------------------------------------------------------
import json
from audit import verify_chain


def test_chain_intact_after_normal_logging(audit_path):
    for i in range(5):
        log_event("a", "llm_call", f"event {i}", path=audit_path)
    r = verify_chain(audit_path)
    assert r["ok"] and r["chained"] == 5 and r["legacy"] == 0


def test_chain_detects_edited_record(audit_path):
    for i in range(3):
        log_event("a", "llm_call", f"event {i}", path=audit_path)
    lines = audit_path.read_text().splitlines()
    rec = json.loads(lines[1]); rec["summary"] = "tampered"
    lines[1] = json.dumps(rec)
    audit_path.write_text("\n".join(lines) + "\n")
    r = verify_chain(audit_path)
    assert not r["ok"] and r["line"] == 2


def test_chain_detects_deleted_record(audit_path):
    for i in range(4):
        log_event("a", "llm_call", f"event {i}", path=audit_path)
    lines = audit_path.read_text().splitlines()
    del lines[1]
    audit_path.write_text("\n".join(lines) + "\n")
    r = verify_chain(audit_path)
    assert not r["ok"] and r["line"] == 2


def test_chain_detects_reordering(audit_path):
    for i in range(3):
        log_event("a", "llm_call", f"event {i}", path=audit_path)
    lines = audit_path.read_text().splitlines()
    lines[0], lines[1] = lines[1], lines[0]
    audit_path.write_text("\n".join(lines) + "\n")
    assert not verify_chain(audit_path)["ok"]


def test_legacy_unchained_file_does_not_crash_and_chains_from_new_records(audit_path):
    legacy = {"id": "x", "timestamp": "2026-01-01T00:00:00+00:00", "agent": "a",
              "kind": "llm_call", "summary": "old", "metadata": {}}
    audit_path.write_text(json.dumps(legacy) + "\n" + json.dumps(dict(legacy, id="y")) + "\n")
    assert verify_chain(audit_path)["ok"]  # legacy-only file is tolerated
    log_event("a", "llm_call", "new1", path=audit_path)
    log_event("a", "llm_call", "new2", path=audit_path)
    r = verify_chain(audit_path)
    assert r["ok"] and r["legacy"] == 2 and r["chained"] == 2
    assert len(read_events(path=audit_path)) == 4
    # editing the first chained record is still detected
    lines = audit_path.read_text().splitlines()
    rec = json.loads(lines[2]); rec["summary"] = "tampered"
    lines[2] = json.dumps(rec)
    audit_path.write_text("\n".join(lines) + "\n")
    assert not verify_chain(audit_path)["ok"]


def test_unchained_line_after_chained_records_is_flagged(audit_path):
    log_event("a", "llm_call", "new", path=audit_path)
    with open(audit_path, "a") as f:
        f.write(json.dumps({"id": "z", "agent": "a", "kind": "k", "summary": "injected", "metadata": {}}) + "\n")
    assert not verify_chain(audit_path)["ok"]


def test_concurrent_audit_writes_keep_chain_valid(audit_path):
    import threading
    ts = [threading.Thread(target=lambda: [log_event("a", "k", "s", path=audit_path) for _ in range(20)])
          for _ in range(6)]
    [t.start() for t in ts]; [t.join() for t in ts]
    r = verify_chain(audit_path)
    assert r["ok"] and r["chained"] == 120


def test_verify_empty_or_missing(tmp_path):
    assert verify_chain(tmp_path / "nope.jsonl")["ok"]
