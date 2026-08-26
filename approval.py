"""
Approval gate for write-side-effect actions (Slack posts, Jira writes,
email sends). Two modes, chosen by the caller, not guessed:

- interactive=True: prompts right there in the terminal (y/N) and blocks
  until answered. Fine for a tool a human is actively running.
- interactive=False: writes a pending-approval record to disk and returns
  immediately without executing the action. A human reviews and runs
  `python cli.py approve <id>` / `deny <id>` later; the action only runs
  once approved — see cli.py. This is the mode a scheduled/unattended
  caller must use, since it can never block on stdin.

Either way, every request and its resolution is written to the audit log.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from audit import log_event
from policy import requires_approval

DEFAULT_PENDING_DIR = Path(__file__).parent / "data" / "pending"


class ApprovalDenied(Exception):
    pass


class ApprovalPending(Exception):
    """Raised in non-interactive mode — the action did NOT execute; it's queued."""
    def __init__(self, request_id):
        self.request_id = request_id
        super().__init__(f"Action queued for approval as {request_id}. Run `python cli.py approve {request_id}` to execute it.")


def _pending_path(request_id: str, pending_dir: Path) -> Path:
    return pending_dir / f"{request_id}.json"


def governed_action(agent_name: str, action_type: str, description: str, execute_fn,
                     interactive: bool = True, pending_dir: Path = DEFAULT_PENDING_DIR):
    """
    Runs execute_fn() only if the action is allowed by policy (auto/none) or
    has been explicitly approved. Returns execute_fn()'s return value if it
    ran. Raises ApprovalPending (non-interactive, newly queued) or
    ApprovalDenied (interactive, user said no) if it didn't.
    """
    if not requires_approval(action_type):
        log_event(agent_name, "write_action_auto_approved", description, {"action_type": action_type})
        return execute_fn()

    if interactive:
        answer = input(f"\n[approval required] {agent_name} wants to: {description}\nApprove? [y/N] ").strip().lower()
        if answer == "y":
            log_event(agent_name, "write_action_approved", description, {"action_type": action_type, "mode": "interactive"})
            result = execute_fn()
            log_event(agent_name, "write_action_executed", description, {"action_type": action_type})
            return result
        else:
            log_event(agent_name, "write_action_denied", description, {"action_type": action_type, "mode": "interactive"})
            raise ApprovalDenied(description)

    # Non-interactive: queue it, never block.
    request_id = str(uuid.uuid4())[:8]
    pending_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "id": request_id,
        "agent": agent_name,
        "action_type": action_type,
        "description": description,
        "status": "pending",
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }
    _pending_path(request_id, pending_dir).write_text(json.dumps(record, indent=2))
    log_event(agent_name, "write_action_queued", description, {"action_type": action_type, "request_id": request_id})
    raise ApprovalPending(request_id)


def list_pending(pending_dir: Path = DEFAULT_PENDING_DIR) -> list:
    if not pending_dir.exists():
        return []
    records = []
    for f in sorted(pending_dir.glob("*.json")):
        record = json.loads(f.read_text())
        if record["status"] == "pending":
            records.append(record)
    return records


def resolve(request_id: str, approved: bool, pending_dir: Path = DEFAULT_PENDING_DIR) -> dict:
    """
    Marks a pending request approved/denied. This does NOT re-execute the
    original action — there's no real way to resume a Python function that
    already returned across a process boundary without seriously
    over-engineering this. Approval here means "cleared to run" — the
    calling agent (or a human) is responsible for re-attempting the action
    on its next run, same as most real approval workflows (a GitHub Actions
    required-reviewer approval triggers a new job run, not a resumed one).
    """
    path = _pending_path(request_id, pending_dir)
    if not path.exists():
        raise FileNotFoundError(f"No pending approval with id {request_id}")
    record = json.loads(path.read_text())
    record["status"] = "approved" if approved else "denied"
    record["resolved_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(record, indent=2))
    log_event(record["agent"], f"write_action_{record['status']}", record["description"],
              {"action_type": record["action_type"], "mode": "async", "request_id": request_id})
    return record
