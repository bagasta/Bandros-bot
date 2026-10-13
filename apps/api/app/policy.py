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
        reasons = {
            RiskClass.EXTERNAL_WRITE: "Tindakan ini mengubah layanan di luar Bandros.",
            RiskClass.DESTRUCTIVE: "Tindakan ini bisa menghapus atau merusak sesuatu.",
            RiskClass.SENSITIVE: "Tindakan ini memakai data sensitif.",
        }
        if risk_class in reasons:
            return PolicyDecision(True, f"{reasons[risk_class]} Setujui sekali untuk melanjutkan.")
        return PolicyDecision(False)

