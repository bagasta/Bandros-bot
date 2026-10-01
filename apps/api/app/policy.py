from __future__ import annotations

from dataclasses import dataclass

from .domain import RiskClass


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    requires_approval: bool
    reason: str | None = None


class PolicyEngine:
    """Enforces action policy outside the model prompt."""

    def decide(self, risk_class: RiskClass) -> PolicyDecision:
        if risk_class in {RiskClass.EXTERNAL_WRITE, RiskClass.DESTRUCTIVE, RiskClass.SENSITIVE}:
            return PolicyDecision(True, f"{risk_class.value} actions require explicit approval")
        return PolicyDecision(False)

