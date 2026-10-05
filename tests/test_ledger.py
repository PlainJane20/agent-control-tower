import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from ledger import BudgetExceededError, check_budget, compute_cost, get_daily_spend, load_ledger, record_usage


@pytest.fixture
def ledger_path(tmp_path):
    return tmp_path / "ledger.json"


def test_compute_cost_known_model():
    cost = compute_cost("claude-sonnet-5", 1_000_000, 0)
    assert cost == 3.00


def test_compute_cost_unknown_model_uses_fallback_not_free():
    cost = compute_cost("some-future-model", 1_000_000, 0)
    assert cost > 0


def test_record_usage_accumulates_across_calls(ledger_path):
    record_usage("agent-a", "claude-sonnet-5", 1000, 500, run_date="2026-08-26", path=ledger_path)
    record_usage("agent-a", "claude-sonnet-5", 2000, 1000, run_date="2026-08-26", path=ledger_path)
    spend = get_daily_spend("agent-a", run_date="2026-08-26", path=ledger_path)
    expected = compute_cost("claude-sonnet-5", 3000, 1500)
    assert spend == pytest.approx(expected)


def test_different_agents_tracked_separately(ledger_path):
    record_usage("agent-a", "claude-sonnet-5", 1_000_000, 0, run_date="2026-08-26", path=ledger_path)
    record_usage("agent-b", "claude-sonnet-5", 1_000_000, 0, run_date="2026-08-26", path=ledger_path)
    assert get_daily_spend("agent-a", "2026-08-26", ledger_path) == pytest.approx(3.00)
    assert get_daily_spend("agent-b", "2026-08-26", ledger_path) == pytest.approx(3.00)


def test_different_days_tracked_separately(ledger_path):
    record_usage("agent-a", "claude-sonnet-5", 1_000_000, 0, run_date="2026-08-25", path=ledger_path)
    record_usage("agent-a", "claude-sonnet-5", 1_000_000, 0, run_date="2026-08-26", path=ledger_path)
    assert get_daily_spend("agent-a", "2026-08-25", ledger_path) == pytest.approx(3.00)
    assert get_daily_spend("agent-a", "2026-08-26", ledger_path) == pytest.approx(3.00)


def test_check_budget_passes_under_cap(ledger_path):
    record_usage("agent-a", "claude-sonnet-5", 100, 100, run_date="2026-08-26", path=ledger_path)
    check_budget("agent-a", daily_cap=10.0, run_date="2026-08-26", path=ledger_path)  # should not raise


def test_check_budget_raises_over_cap(ledger_path):
    record_usage("agent-a", "claude-sonnet-5", 1_000_000, 0, run_date="2026-08-26", path=ledger_path)  # $3
    with pytest.raises(BudgetExceededError):
        check_budget("agent-a", daily_cap=1.0, run_date="2026-08-26", path=ledger_path)


def test_check_budget_none_cap_never_raises(ledger_path):
    record_usage("agent-a", "claude-sonnet-5", 100_000_000, 0, run_date="2026-08-26", path=ledger_path)
    check_budget("agent-a", daily_cap=None, run_date="2026-08-26", path=ledger_path)  # should not raise


# --- concurrency ----------------------------------------------------------------
import subprocess
import threading


def test_concurrent_threads_do_not_lose_updates(tmp_path):
    path = tmp_path / "ledger.json"
    n_threads, n_each = 8, 25
    ts = [threading.Thread(target=lambda: [record_usage("a", "claude-sonnet-5", 10, 5, run_date="2026-01-01", path=path)
                                           for _ in range(n_each)]) for _ in range(n_threads)]
    [t.start() for t in ts]; [t.join() for t in ts]
    stats = load_ledger(path)["2026-01-01"]["a"]
    assert stats["calls"] == n_threads * n_each
    assert stats["input_tokens"] == 10 * n_threads * n_each


def test_concurrent_processes_do_not_lose_updates(tmp_path):
    path = tmp_path / "ledger.json"
    repo = Path(__file__).parent.parent
    code = (f"import sys; sys.path.insert(0, {str(repo)!r}); from pathlib import Path; from ledger import record_usage\n"
            f"[record_usage('a','claude-sonnet-5',1,1,run_date='2026-01-01',path=Path({str(path)!r})) for _ in range(40)]")
    procs = [subprocess.Popen([sys.executable, "-c", code]) for _ in range(5)]
    assert all(p.wait() == 0 for p in procs)
    assert load_ledger(path)["2026-01-01"]["a"]["calls"] == 200
