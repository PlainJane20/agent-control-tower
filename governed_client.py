"""
Thin wrapper around anthropic.Anthropic that makes cost tracking and audit
logging automatic instead of something every calling agent has to remember
to do itself. Drop-in enough that retrofitting an existing agent is a
one-line change to how the client is constructed, not a rewrite of its
summarization logic.
"""

import anthropic

from audit import log_event
from ledger import BudgetExceededError, check_budget, record_usage


class _Messages:
    """Mirrors anthropic.Anthropic().messages so client.messages.create(...)
    works identically whether client is a GovernedClient or the real SDK —
    a true drop-in, not a lookalike with a different call shape."""

    def __init__(self, outer: "GovernedClient"):
        self._outer = outer

    def create(self, **kwargs):
        return self._outer._governed_create(**kwargs)


class GovernedClient:
    def __init__(self, agent_name: str, api_key: str, daily_budget: float = None):
        self.agent_name = agent_name
        self.daily_budget = daily_budget
        self._client = anthropic.Anthropic(api_key=api_key)
        self.messages = _Messages(self)

    def _governed_create(self, **kwargs):
        check_budget(self.agent_name, self.daily_budget)  # raises BudgetExceededError if already over

        response = self._client.messages.create(**kwargs)

        usage = response.usage
        cost = record_usage(
            self.agent_name,
            kwargs.get("model", "unknown"),
            usage.input_tokens,
            usage.output_tokens,
        )
        log_event(
            self.agent_name, "llm_call",
            f"{kwargs.get('model')} call — {usage.input_tokens} in / {usage.output_tokens} out",
            {"cost_usd": round(cost, 6), "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens},
        )
        return response


__all__ = ["GovernedClient", "BudgetExceededError"]
