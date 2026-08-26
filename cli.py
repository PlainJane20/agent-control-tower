#!/usr/bin/env python3
"""
Governance CLI — inspect spend, audit trail, and pending approvals.

Usage:
  python cli.py ledger [--agent NAME] [--date YYYY-MM-DD]
  python cli.py audit [--agent NAME] [--kind KIND]
  python cli.py pending
  python cli.py approve <request-id>
  python cli.py deny <request-id>
"""

import argparse
import json
from datetime import date

from rich.console import Console
from rich.table import Table

from approval import list_pending, resolve
from audit import read_events
from ledger import load_ledger, DEFAULT_LEDGER_PATH

console = Console()


def cmd_ledger(args):
    data = load_ledger(DEFAULT_LEDGER_PATH)
    run_date = args.date or date.today().isoformat()
    day = data.get(run_date, {})
    table = Table(title=f"Spend for {run_date}")
    table.add_column("Agent")
    table.add_column("Calls", justify="right")
    table.add_column("Input tokens", justify="right")
    table.add_column("Output tokens", justify="right")
    table.add_column("Cost (USD)", justify="right")
    rows = day.items() if not args.agent else [(args.agent, day.get(args.agent, {}))]
    total = 0.0
    for agent, stats in rows:
        if not stats:
            continue
        table.add_row(agent, str(stats["calls"]), str(stats["input_tokens"]),
                      str(stats["output_tokens"]), f"${stats['cost']:.4f}")
        total += stats["cost"]
    console.print(table)
    console.print(f"[bold]Total:[/] ${total:.4f}")


def cmd_audit(args):
    events = read_events(agent_name=args.agent, kind=args.kind)
    table = Table(title="Audit log")
    table.add_column("Time")
    table.add_column("Agent")
    table.add_column("Kind")
    table.add_column("Summary")
    for e in events[-args.limit:]:
        table.add_row(e["timestamp"][:19], e["agent"], e["kind"], e["summary"][:60])
    console.print(table)


def cmd_pending(args):
    records = list_pending()
    if not records:
        console.print("[dim]No pending approvals.[/]")
        return
    table = Table(title="Pending approvals")
    table.add_column("ID")
    table.add_column("Agent")
    table.add_column("Action type")
    table.add_column("Description")
    table.add_column("Requested at")
    for r in records:
        table.add_row(r["id"], r["agent"], r["action_type"], r["description"][:50], r["requested_at"][:19])
    console.print(table)


def cmd_approve(args):
    record = resolve(args.request_id, approved=True)
    console.print(f"[green]✓ Approved[/] {record['id']} — {record['description']}")
    console.print("[dim]Note: this marks it cleared. Re-run the originating agent to execute it.[/]")


def cmd_deny(args):
    record = resolve(args.request_id, approved=False)
    console.print(f"[red]✗ Denied[/] {record['id']} — {record['description']}")


def main():
    parser = argparse.ArgumentParser(description="Agent Control Tower — governance CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ledger = sub.add_parser("ledger")
    p_ledger.add_argument("--agent")
    p_ledger.add_argument("--date")
    p_ledger.set_defaults(func=cmd_ledger)

    p_audit = sub.add_parser("audit")
    p_audit.add_argument("--agent")
    p_audit.add_argument("--kind")
    p_audit.add_argument("--limit", type=int, default=20)
    p_audit.set_defaults(func=cmd_audit)

    p_pending = sub.add_parser("pending")
    p_pending.set_defaults(func=cmd_pending)

    p_approve = sub.add_parser("approve")
    p_approve.add_argument("request_id")
    p_approve.set_defaults(func=cmd_approve)

    p_deny = sub.add_parser("deny")
    p_deny.add_argument("request_id")
    p_deny.set_defaults(func=cmd_deny)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
