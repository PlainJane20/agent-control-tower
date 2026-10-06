"""
Approval gate for write-side-effect actions (Slack posts, Jira writes,
email sends). Two modes, chosen by the caller, not guessed:

- interactive=True: prompts right there in the terminal (y/N) and blocks
  until answered. Fine for a tool a human is actively running.
- interactive=False: never blocks. On a gated action it first looks for an
  APPROVED, unconsumed, unexpired request matching (agent, action_type,
  sha256 of the description). If found it atomically marks it consumed
  (single use), audit-logs that, and runs the action. Otherwise it queues a
  pending request (reusing an identical still-pending one rather than
  creating a duplicate) and raises ApprovalPending. A human reviews with
  `python cli.py approve <id>` / `deny <id>`; the NEXT run of the calling
  agent then executes the action. This is the mode a scheduled/unattended
  caller must use, since it can never block on stdin.

Approvals expire (default 24h from approval time; override per call with
approval_ttl_seconds or globally with env ACT_APPROVAL_TTL_SECONDS).
A request is marked consumed BEFORE execute_fn runs, so a crash or failure
inside execute_fn does not allow a silent retry (at-most-once, not
exactly-once); re-queue by calling again after the failure is understood.

Either way, every request and its resolution is written to the audit log.
"""

import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from audit import log_event
from filelock import file_lock
from policy import requires_approval

DEFAULT_PENDING_DIR = Path(__file__).parent / "data" / "pending"
DEFAULT_APPROVAL_TTL_SECONDS = 24 * 60 * 60


class ApprovalDenied(Exception):
    pass


class ApprovalPending(Exception):
    """Raised in non-interactive mode — the action did NOT execute; it's queued."""
    def __init__(self, request_id):
        self.request_id = request_id
        super().__init__(f"Action queued for approval as {request_id}. Run `python cli.py approve {request_id}` to execute it.")


def _pending_path(request_id: str, pending_dir: Path) -> Path:
    return pending_dir / f"{request_id}.json"


def _lock(pending_dir: Path):
    return file_lock(pending_dir / ".lock")


def description_hash(description: str) -> str:
    return hashlib.sha256(description.encode("utf-8")).hexdigest()


def _ttl_seconds(approval_ttl_seconds):
    if approval_ttl_seconds is not None:
        return approval_ttl_seconds
    env = os.environ.get("ACT_APPROVAL_TTL_SECONDS")
    return float(env) if env else DEFAULT_APPROVAL_TTL_SECONDS


def _write(record: dict, pending_dir: Path):
    path = _pending_path(record["id"], pending_dir)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(record, indent=2))
    os.replace(tmp, path)


def _records(pending_dir: Path):
    for f in sorted(pending_dir.glob("*.json")):
        try:
            yield json.loads(f.read_text())
        except json.JSONDecodeError:
            continue


def _matches(record: dict, agent_name: str, action_type: str, dhash: str) -> bool:
    return (record.get("agent") == agent_name
            and record.get("action_type") == action_type
            and (record.get("description_hash") or description_hash(record.get("description", ""))) == dhash)


def _is_expired(record: dict, ttl: float, now: datetime) -> bool:
    approved_at = datetime.fromisoformat(record.get("resolved_at") or record["requested_at"])
    return now - approved_at > timedelta(seconds=ttl)


def governed_action(agent_name: str, action_type: str, description: str, execute_fn,
                     interactive: bool = True, pending_dir: Path = DEFAULT_PENDING_DIR,
                     approval_ttl_seconds: float = None):
    """
    Runs execute_fn() only if the action is allowed by policy (auto/none) or
    has been explicitly approved. Returns execute_fn()'s return value if it
    ran. Raises ApprovalPending (non-interactive, nothing approved yet) or
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

    # Non-interactive: consume a matching approval if one exists, else queue. Never block on stdin.
    dhash = description_hash(description)
    ttl = _ttl_seconds(approval_ttl_seconds)
    pending_dir.mkdir(parents=True, exist_ok=True)
    consumed = None
    with _lock(pending_dir):
        now = datetime.now(timezone.utc)
        existing_pending = None
        for record in _records(pending_dir):
            if not _matches(record, agent_name, action_type, dhash):
                continue
            if record["status"] == "approved" and not _is_expired(record, ttl, now):
                consumed = record
                break
            if record["status"] == "pending" and existing_pending is None:
                existing_pending = record
        if consumed is not None:
            consumed["status"] = "consumed"
            consumed["consumed_at"] = now.isoformat()
            _write(consumed, pending_dir)
            log_event(agent_name, "write_action_approval_consumed", description,
                      {"action_type": action_type, "mode": "async", "request_id": consumed["id"]})
        elif existing_pending is not None:
            request_id = existing_pending["id"]
        else:
            request_id = str(uuid.uuid4())[:8]
            _write({
                "id": request_id,
                "agent": agent_name,
                "action_type": action_type,
                "description": description,
                "description_hash": dhash,
                "status": "pending",
                "requested_at": now.isoformat(),
            }, pending_dir)
            log_event(agent_name, "write_action_queued", description, {"action_type": action_type, "request_id": request_id})

    if consumed is None:
        raise ApprovalPending(request_id)

    # Run outside the lock so a slow action doesn't block other approvals.
    result = execute_fn()
    log_event(agent_name, "write_action_executed", description,
              {"action_type": action_type, "mode": "async", "request_id": consumed["id"]})
    return result


def list_pending(pending_dir: Path = DEFAULT_PENDING_DIR) -> list:
    if not pending_dir.exists():
        return []
    return [r for r in _records(pending_dir) if r["status"] == "pending"]


def resolve(request_id: str, approved: bool, pending_dir: Path = DEFAULT_PENDING_DIR) -> dict:
    """
    Marks a pending request approved/denied. It does NOT run the action; the
    calling agent's next non-interactive governed_action() call with the same
    agent, action type and description consumes an approved request (once,
    within the expiry window). Only a still-pending request can be resolved,
    so a consumed or denied request cannot be flipped back into a grant.
    NOTE: no authentication - anyone who can run this can approve.
    """
    path = _pending_path(request_id, pending_dir)
    if not path.exists():
        raise FileNotFoundError(f"No pending approval with id {request_id}")
    with _lock(pending_dir):
        record = json.loads(path.read_text())
        if record["status"] != "pending":
            raise ValueError(f"Request {request_id} is already {record['status']}; cannot resolve again")
        record["status"] = "approved" if approved else "denied"
        record["resolved_at"] = datetime.now(timezone.utc).isoformat()
        _write(record, pending_dir)
    log_event(record["agent"], f"write_action_{record['status']}", record["description"],
              {"action_type": record["action_type"], "mode": "async", "request_id": request_id})
    return record
