"""
Cost ledger with per-agent daily budget caps.

Pricing is approximate and intentionally configurable, not hardcoded truth —
list prices change, and this isn't meant to be a billing system of record,
just enough signal to catch a runaway agent before it burns real money.
"""

import json
from datetime import date
from pathlib import Path

DEFAULT_LEDGER_PATH = Path(__file__).parent / "data" / "ledger.json"

# $ per million tokens (input, output). Update if Anthropic's pricing changes —
# this table is the one place that assumption lives.
PRICING = {
    "claude-sonnet-5": (3.00, 15.00),
    "claude-opus-5": (15.00, 75.00),
    "claude-haiku-4-5-20251001": (0.80, 4.00),
}
DEFAULT_PRICE = (3.00, 15.00)  # fallback for an unlisted model, not a silent free ride


class BudgetExceededError(Exception):
    pass


def compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    in_price, out_price = PRICING.get(model, DEFAULT_PRICE)
    return (input_tokens / 1_000_000) * in_price + (output_tokens / 1_000_000) * out_price


def load_ledger(path: Path = DEFAULT_LEDGER_PATH) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


_load = load_ledger  # internal alias used by the functions below


def _save(data: dict, path: Path):
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def record_usage(
    agent_name: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    run_date: str = None,
    path: Path = DEFAULT_LEDGER_PATH,
) -> float:
    """Records usage and returns the cost of this call in USD."""
    run_date = run_date or date.today().isoformat()
    cost = compute_cost(model, input_tokens, output_tokens)

    data = _load(path)
    day = data.setdefault(run_date, {})
    agent = day.setdefault(agent_name, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost": 0.0})
    agent["calls"] += 1
    agent["input_tokens"] += input_tokens
    agent["output_tokens"] += output_tokens
    agent["cost"] += cost
    _save(data, path)
    return cost


def get_daily_spend(agent_name: str, run_date: str = None, path: Path = DEFAULT_LEDGER_PATH) -> float:
    run_date = run_date or date.today().isoformat()
    data = _load(path)
    return data.get(run_date, {}).get(agent_name, {}).get("cost", 0.0)


def check_budget(agent_name: str, daily_cap: float, run_date: str = None, path: Path = DEFAULT_LEDGER_PATH):
    """Raises BudgetExceededError if the agent's spend today is already at or over the cap."""
    if daily_cap is None:
        return
    spent = get_daily_spend(agent_name, run_date, path)
    if spent >= daily_cap:
        raise BudgetExceededError(
            f"{agent_name} has spent ${spent:.4f} today, at or above its ${daily_cap:.2f} daily cap."
        )
