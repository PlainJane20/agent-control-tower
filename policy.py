"""
Declarative risk policy — the thing a governance reviewer actually reads,
not something buried in if/else branches inside application code.

Design constraint this had to solve: slack-daily-agent runs unattended via
launchd every morning (see its README's "verified unattended execution"
section) — an approval gate that blocks on stdin would silently break a
tool that was specifically hardened to never hang on input. So the policy
is action-type-scoped, not global:

- "llm_call" is never gated — it's read-only against Claude, cost/budget
  tracking is the relevant control, not approval.
- "slack_post_recurring" (posting the same scheduled brief to a pre-approved
  channel) auto-approves — it's the same low-risk action every day, already
  reviewed once. This is what keeps the unattended path unattended.
- "slack_post_adhoc" and anything higher-stakes (Jira writes, email sends)
  require approval every time, in whichever mode the caller runs in
  (interactive prompt or async pending-queue) — see approval.py.
"""

RISK_POLICY = {
    "llm_call": "none",
    "slack_post_recurring": "auto",
    "slack_post_adhoc": "approval",
    "jira_write": "approval",
    "email_send": "approval",
}


def requires_approval(action_type: str) -> bool:
    return RISK_POLICY.get(action_type, "approval") == "approval"
