import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
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
