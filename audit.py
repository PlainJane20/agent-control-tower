"""
Append-only audit log. One JSON line per event — never rewritten, never
deleted, so the log itself can't be quietly edited after the fact. This is
the "what actually happened" record; ledger.py is "what it cost."
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_AUDIT_PATH = Path(__file__).parent / "data" / "audit.jsonl"


def log_event(agent_name: str, kind: str, summary: str, metadata: dict = None,
              path: Path = DEFAULT_AUDIT_PATH) -> str:
    """
    kind: e.g. "llm_call", "write_action_approved", "write_action_denied",
    "write_action_executed". Returns the event id.
    """
    event = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": agent_name,
        "kind": kind,
        "summary": summary,
        "metadata": metadata or {},
    }
    path.parent.mkdir(exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(event) + "\n")
    return event["id"]


def read_events(agent_name: str = None, kind: str = None, path: Path = DEFAULT_AUDIT_PATH) -> list:
    if not path.exists():
        return []
    events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if agent_name:
        events = [e for e in events if e["agent"] == agent_name]
    if kind:
        events = [e for e in events if e["kind"] == kind]
    return events
