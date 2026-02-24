"""
Adaptive model escalation — detects when the current model is struggling
and recommends upgrading to a more capable one mid-task.

Triggers:
  - 3+ consecutive tool errors
  - Same tool call repeated 3+ times (looping)
  - >15 iterations with recent errors (no progress)

Escalation chain:  deepseek → openai → claude

Usage:
    from agents.escalation import EscalationState, make_args_hash

    esc = EscalationState(current_provider="deepseek")
    esc.record_tool_result("execute_command", make_args_hash(args), success=False)
    if esc.should_escalate():
        target = esc.get_escalation_target()
        esc.mark_escalated(target)
"""

import hashlib
import json
from dataclasses import dataclass, field


@dataclass
class EscalationState:
    """Tracks agent performance to decide when to escalate the model.

    Supports multi-step escalation: deepseek → openai → claude.
    After reaching the top of the chain, further escalation is blocked but
    loop detection still works to force early termination.
    """

    current_provider: str = "deepseek"
    escalated: bool = False

    # ── Internal counters ─────────────────────────────────────────
    consecutive_errors: int = 0
    total_iterations: int = 0
    _last_call_sigs: list = field(default_factory=list)
    _escalation_count: int = 0
    _iterations_since_escalation: int = 0

    # ── Configuration ─────────────────────────────────────────────
    MAX_CONSECUTIVE_ERRORS: int = 3
    MAX_REPEATED_CALLS: int = 3
    STALL_ITERATION_THRESHOLD: int = 15
    MAX_ESCALATIONS: int = 2  # deepseek→openai→claude = 2 hops max
    COOLDOWN_AFTER_ESCALATION: int = 5  # min iterations before re-escalation

    def record_tool_result(self, tool_name: str, args_hash: str, success: bool) -> None:
        """Record the outcome of a tool execution."""
        self.total_iterations += 1
        self._iterations_since_escalation += 1

        if success:
            self.consecutive_errors = 0
        else:
            self.consecutive_errors += 1

        sig = f"{tool_name}:{args_hash}"
        self._last_call_sigs.append(sig)
        if len(self._last_call_sigs) > 12:
            self._last_call_sigs = self._last_call_sigs[-12:]

    def should_escalate(self) -> bool:
        """Should we switch to a more capable model?"""
        if self._escalation_count >= self.MAX_ESCALATIONS:
            return False

        # Cooldown: don't re-escalate too quickly after the last one
        if self.escalated and self._iterations_since_escalation < self.COOLDOWN_AFTER_ESCALATION:
            return False

        if self.consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS:
            return True

        n = self.MAX_REPEATED_CALLS
        if len(self._last_call_sigs) >= n:
            last_n = self._last_call_sigs[-n:]
            if len(set(last_n)) == 1:
                return True

        if (self.total_iterations >= self.STALL_ITERATION_THRESHOLD
                and self.consecutive_errors > 0):
            return True

        return False

    @property
    def is_stuck(self) -> bool:
        """True if agent is still failing after exhausting all escalations."""
        if self._escalation_count < self.MAX_ESCALATIONS:
            return False
        return (self.consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS
                or self._is_looping())

    def _is_looping(self) -> bool:
        n = self.MAX_REPEATED_CALLS
        if len(self._last_call_sigs) >= n:
            return len(set(self._last_call_sigs[-n:])) == 1
        return False

    def get_escalation_target(self) -> str:
        """Which provider to escalate to."""
        chain = {
            "deepseek": "openai",
            "qwen":     "openai",
            "openai":   "claude",
        }
        return chain.get(self.current_provider, "claude")

    def mark_escalated(self, new_provider: str) -> None:
        """Mark escalation as done and reset error counters."""
        self.escalated = True
        self._escalation_count += 1
        self._iterations_since_escalation = 0
        self.current_provider = new_provider
        self.consecutive_errors = 0
        self._last_call_sigs.clear()

    def get_escalation_hint(self) -> str:
        """System-level hint to inject into messages after escalation."""
        return (
            "[SYSTEM NOTE: Previous attempts had repeated errors. "
            "Re-analyze the problem from scratch. "
            "Do NOT repeat the same failing approach — try an alternative.]"
        )


def make_args_hash(tool_args: dict) -> str:
    """Fast 8-char hash of tool arguments for loop detection."""
    try:
        raw = json.dumps(tool_args, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        raw = str(tool_args)
    return hashlib.md5(raw.encode()).hexdigest()[:8]
