"""
Audit log. One JSON line per event, appended to a JSONL file; this module
never rewrites or deletes entries. Records are hash-chained: each new
record stores "prev_hash" (the previous record's hash, or GENESIS for the
first chained record) and "hash" = sha256(prev_hash + canonical JSON of the
record body). verify_chain() detects edits, deletions, reordering and
insertions within the chained part of the file.

Limits (this is tamper-EVIDENCE, not tamper-proofing):
- Anyone with write access can recompute the whole chain after editing, or
  truncate the tail, and verification will still pass; there is no
  signature or external anchor. Keep the latest hash somewhere else if you
  need to detect that.
- Lines written before chaining existed ("legacy prefix") have no hash and
  are not protected; the chain starts at the first record that has one. A
  legacy-style line appearing after a chained record is reported as a failure.

This is the "what happened" record; ledger.py is "what it cost."
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from filelock import file_lock

DEFAULT_AUDIT_PATH = Path(__file__).parent / "data" / "audit.jsonl"
GENESIS = "0" * 64


def _compute_hash(prev_hash: str, body: dict) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256((prev_hash + canonical).encode("utf-8")).hexdigest()


def _body(record: dict) -> dict:
    return {k: v for k, v in record.items() if k not in ("hash", "prev_hash")}


def _last_hash(path: Path) -> str:
    """Hash of the last chained record, or GENESIS if none (empty/legacy-only file)."""
    if not path.exists():
        return GENESIS
    last = None
    with open(path) as f:
        for line in f:
            if line.strip():
                last = line
    if last is None:
        return GENESIS
    try:
        return json.loads(last).get("hash", GENESIS)
    except (json.JSONDecodeError, AttributeError):
        return GENESIS  # corrupt tail: verify_chain will flag it


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
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path.with_name(path.name + ".lock")):
        prev = _last_hash(path)
        event["prev_hash"] = prev
        event["hash"] = _compute_hash(prev, _body(event))
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


def verify_chain(path: Path = DEFAULT_AUDIT_PATH) -> dict:
    """
    Returns {"ok": bool, "legacy": n_unchained_prefix_lines, "chained": n,
    "error": str|None, "line": 1-based line of first problem|None}.
    """
    result = {"ok": True, "legacy": 0, "chained": 0, "error": None, "line": None}
    if not path.exists():
        return result

    def fail(lineno, msg):
        result.update(ok=False, error=msg, line=lineno)
        return result

    prev = GENESIS
    with open(path) as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                return fail(lineno, "unparseable line")
            if not isinstance(rec, dict):
                return fail(lineno, "record is not an object")
            if "hash" not in rec:
                if result["chained"]:
                    return fail(lineno, "unchained record after chained records")
                result["legacy"] += 1
                continue
            if rec.get("prev_hash") != prev:
                return fail(lineno, "prev_hash does not match previous record (deleted, inserted or reordered line)")
            if _compute_hash(prev, _body(rec)) != rec["hash"]:
                return fail(lineno, "hash mismatch (record was modified)")
            prev = rec["hash"]
            result["chained"] += 1
    return result
