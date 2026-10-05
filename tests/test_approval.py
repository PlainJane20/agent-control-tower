import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import json
from datetime import datetime, timedelta, timezone

import approval
from approval import ApprovalDenied, ApprovalPending, governed_action, list_pending, resolve


@pytest.fixture(autouse=True)
def no_real_audit_writes(monkeypatch):
    # governed_action logs to the real audit.jsonl by default — redirect to
    # a no-op so tests don't pollute the actual data/audit.jsonl on disk.
    # (audit.py itself is unit-tested separately in test_audit.py.)
    monkeypatch.setattr(approval, "log_event", lambda *a, **k: "test-event-id")


@pytest.fixture
def pending_dir(tmp_path):
    return tmp_path / "pending"


def test_none_risk_action_runs_without_approval(pending_dir):
    executed = []
    result = governed_action("agent-a", "llm_call", "read something", lambda: executed.append(1) or "ok",
                              interactive=True, pending_dir=pending_dir)
    assert result == "ok"
    assert executed == [1]


def test_auto_risk_action_runs_without_approval(pending_dir):
    result = governed_action("slack-daily-agent", "slack_post_recurring", "post the daily brief",
                              lambda: "posted", interactive=True, pending_dir=pending_dir)
    assert result == "posted"


def test_interactive_approval_yes_executes_action(monkeypatch, pending_dir):
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    result = governed_action("agent-a", "jira_write", "close ticket X", lambda: "closed",
                              interactive=True, pending_dir=pending_dir)
    assert result == "closed"


def test_interactive_approval_no_raises_denied(monkeypatch, pending_dir):
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    with pytest.raises(ApprovalDenied):
        governed_action("agent-a", "jira_write", "close ticket X", lambda: "closed",
                         interactive=True, pending_dir=pending_dir)


def test_noninteractive_gated_action_queues_instead_of_executing(pending_dir):
    executed = []
    with pytest.raises(ApprovalPending):
        governed_action("agent-a", "email_send", "send exec email", lambda: executed.append(1),
                         interactive=False, pending_dir=pending_dir)
    assert executed == []  # never ran
    pending = list_pending(pending_dir)
    assert len(pending) == 1
    assert pending[0]["description"] == "send exec email"


def test_approve_marks_status_but_does_not_execute(pending_dir):
    executed = []
    try:
        governed_action("agent-a", "email_send", "send exec email", lambda: executed.append(1),
                         interactive=False, pending_dir=pending_dir)
    except ApprovalPending as e:
        request_id = e.request_id

    record = resolve(request_id, approved=True, pending_dir=pending_dir)
    assert record["status"] == "approved"
    assert executed == []  # approving does not retroactively run it
    assert list_pending(pending_dir) == []  # no longer shows as pending


def test_deny_marks_status_denied(pending_dir):
    try:
        governed_action("agent-a", "email_send", "send exec email", lambda: None,
                         interactive=False, pending_dir=pending_dir)
    except ApprovalPending as e:
        request_id = e.request_id

    record = resolve(request_id, approved=False, pending_dir=pending_dir)
    assert record["status"] == "denied"


def test_resolve_unknown_id_raises():
    with pytest.raises(FileNotFoundError):
        resolve("does-not-exist", approved=True, pending_dir=Path("/tmp/nonexistent-pending-dir-xyz"))


# --- async approval actually gates execution ---------------------------------

def _queue(pending_dir, desc="send exec email", agent="agent-a", action="email_send"):
    with pytest.raises(ApprovalPending) as e:
        governed_action(agent, action, desc, lambda: None, interactive=False, pending_dir=pending_dir)
    return e.value.request_id


def test_approved_request_executes_once_then_requeues(pending_dir):
    request_id = _queue(pending_dir)
    resolve(request_id, approved=True, pending_dir=pending_dir)
    executed = []
    result = governed_action("agent-a", "email_send", "send exec email",
                             lambda: executed.append(1) or "sent", interactive=False, pending_dir=pending_dir)
    assert result == "sent" and executed == [1]
    # single use: second identical call must NOT execute, and queues a fresh request
    with pytest.raises(ApprovalPending) as e:
        governed_action("agent-a", "email_send", "send exec email",
                        lambda: executed.append(2), interactive=False, pending_dir=pending_dir)
    assert executed == [1]
    assert e.value.request_id != request_id


def test_expired_approval_is_rejected(pending_dir):
    request_id = _queue(pending_dir)
    resolve(request_id, approved=True, pending_dir=pending_dir)
    path = pending_dir / f"{request_id}.json"
    rec = json.loads(path.read_text())
    rec["resolved_at"] = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    path.write_text(json.dumps(rec))
    executed = []
    with pytest.raises(ApprovalPending):
        governed_action("agent-a", "email_send", "send exec email",
                        lambda: executed.append(1), interactive=False, pending_dir=pending_dir)
    assert executed == []


def test_ttl_is_configurable(pending_dir, monkeypatch):
    request_id = _queue(pending_dir)
    resolve(request_id, approved=True, pending_dir=pending_dir)
    executed = []
    with pytest.raises(ApprovalPending):  # ttl of -1s: everything is already expired
        governed_action("agent-a", "email_send", "send exec email", lambda: executed.append(1),
                        interactive=False, pending_dir=pending_dir, approval_ttl_seconds=-1)
    assert executed == []
    monkeypatch.setenv("ACT_APPROVAL_TTL_SECONDS", "3600")
    governed_action("agent-a", "email_send", "send exec email", lambda: executed.append(1),
                    interactive=False, pending_dir=pending_dir)
    assert executed == [1]


@pytest.mark.parametrize("agent,action,desc", [
    ("agent-a", "email_send", "send a DIFFERENT email"),
    ("agent-b", "email_send", "send exec email"),
    ("agent-a", "jira_write", "send exec email"),
])
def test_mismatched_request_does_not_use_approval(pending_dir, agent, action, desc):
    request_id = _queue(pending_dir)
    resolve(request_id, approved=True, pending_dir=pending_dir)
    executed = []
    with pytest.raises(ApprovalPending):
        governed_action(agent, action, desc, lambda: executed.append(1), interactive=False, pending_dir=pending_dir)
    assert executed == []


def test_denied_request_never_executes(pending_dir):
    request_id = _queue(pending_dir)
    resolve(request_id, approved=False, pending_dir=pending_dir)
    executed = []
    with pytest.raises(ApprovalPending):
        governed_action("agent-a", "email_send", "send exec email", lambda: executed.append(1),
                        interactive=False, pending_dir=pending_dir)
    assert executed == []


def test_identical_pending_request_not_duplicated(pending_dir):
    first = _queue(pending_dir)
    second = _queue(pending_dir)
    assert first == second
    assert len(list_pending(pending_dir)) == 1


def test_cannot_resolve_twice(pending_dir):
    request_id = _queue(pending_dir)
    resolve(request_id, approved=False, pending_dir=pending_dir)
    with pytest.raises(ValueError):
        resolve(request_id, approved=True, pending_dir=pending_dir)


def test_consumption_is_audited(pending_dir, monkeypatch):
    events = []
    monkeypatch.setattr(approval, "log_event", lambda agent, kind, *a, **k: events.append(kind))
    request_id = _queue(pending_dir)
    resolve(request_id, approved=True, pending_dir=pending_dir)
    governed_action("agent-a", "email_send", "send exec email", lambda: None,
                    interactive=False, pending_dir=pending_dir)
    assert "write_action_approval_consumed" in events
    assert events[-1] == "write_action_executed"
