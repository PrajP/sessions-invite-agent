"""Programmatic Self-Evaluation Policy Guardrails for Payment Reconciliation Agent.

Enforces zero-trust policy hooks that deterministically block session enrollment
and calendar dispatch if payment verification or identity checks fail.
"""

from typing import Any, Dict, Optional, Tuple
from pydantic import BaseModel, Field

from src.logging_tracer import log_agent_lifecycle


class PolicyEvaluationResult(BaseModel):
    """Result of programmatic guardrail self-evaluation."""

    allowed: bool = Field(..., description="Whether action is permitted to proceed.")
    policy_code: str = Field(..., description="Policy status code: 'PASSED', 'PAYMENT_UNVERIFIED', 'HITL_REQUIRED', 'FEE_MISMATCH', 'LOW_CONFIDENCE'.")
    reason: str = Field(..., description="Human-readable explanation of guardrail decision.")
    remediation: Optional[str] = Field(default=None, description="Steps required to satisfy policy.")


# Strict Pricing Matrix for Sessions
SESSION_FEES = {
    1: {"INR": 4000.0, "USD": 50.0},
    2: {"INR": 8000.0, "USD": 100.0},
    3: {"INR": 12000.0, "USD": 150.0},
}


def validate_session_fee(session_level: int, amount: float, currency: str) -> Tuple[bool, str]:
    """Validates that payment amount matches expected session fee for the level.

    Args:
        session_level: Course level (1, 2, or 3).
        amount: Paid amount.
        currency: Currency ('INR' or 'USD').

    Returns:
        Tuple of (is_valid, message).
    """
    currency_norm = currency.upper().strip()
    if session_level not in SESSION_FEES:
        return False, f"Invalid session level {session_level}. Must be 1, 2, or 3."

    if currency_norm not in SESSION_FEES[session_level]:
        return False, f"Unsupported currency '{currency_norm}'. Only INR and USD are accepted."

    expected = SESSION_FEES[session_level][currency_norm]
    if amount < expected:
        return False, f"Underpayment: Level {session_level} requires {currency_norm} {expected:.2f}, but received {amount:.2f}."

    if amount > expected:
        return True, f"Overpayment detected: Received {currency_norm} {amount:.2f} (Required: {expected:.2f}). Eligible for credit."

    return True, f"Exact payment matched for Level {session_level} ({currency_norm} {expected:.2f})."


def evaluate_dispatch_policy(
    is_payment_verified: bool,
    confidence_score: float,
    is_third_party_payer: bool = False,
    hitl_approved: bool = False,
) -> PolicyEvaluationResult:
    """Programmatic self-evaluation guardrail hook that gates calendar and email dispatch.

    Cardinal Rules:
    1. If payment is unverified -> HALT & BLOCK dispatch.
    2. If third-party payer detected without human approval -> HALT & ESCALATE to HITL.
    3. If OCR confidence score < 0.85 without human approval -> HALT & ESCALATE.

    Args:
        is_payment_verified: Output flag from bank statement verification.
        confidence_score: OCR confidence score from receipt analysis (0.0 - 1.0).
        is_third_party_payer: True if payer does not match applicant registration info.
        hitl_approved: True if a human administrator clicked the signed approval link.

    Returns:
        PolicyEvaluationResult detailing whether dispatch is allowed.
    """
    intent = "Evaluate dispatch policy guardrail"

    # Rule 1: Zero unverified meeting invites
    if not is_payment_verified and not hitl_approved:
        log_agent_lifecycle(intent=intent, outcome="BLOCKED_PAYMENT_UNVERIFIED", level="error")
        return PolicyEvaluationResult(
            allowed=False,
            policy_code="PAYMENT_UNVERIFIED",
            reason="Violation: Attempted to dispatch session calendar invite for unverified payment.",
            remediation="Reconcile transaction with bank statement or request human admin review.",
        )

    # Rule 2: Third-party payer requires explicit human sign-off
    if is_third_party_payer and not hitl_approved:
        log_agent_lifecycle(intent=intent, outcome="BLOCKED_HITL_REQUIRED", level="warning")
        return PolicyEvaluationResult(
            allowed=False,
            policy_code="HITL_REQUIRED",
            reason="Violation: Third-party payer (family/friend) detected. Administrator confirmation required.",
            remediation="Generate 1-click signed HITL approval ticket and notify administrator via WhatsApp/Email.",
        )

    # Rule 3: Low OCR confidence requires human validation
    if confidence_score < 0.85 and not hitl_approved:
        log_agent_lifecycle(intent=intent, outcome="BLOCKED_LOW_CONFIDENCE", level="warning")
        return PolicyEvaluationResult(
            allowed=False,
            policy_code="LOW_CONFIDENCE",
            reason=f"Violation: Receipt extraction confidence ({confidence_score:.2f}) below threshold 0.85.",
            remediation="Prompt applicant for a clearer receipt screenshot or request admin manual check.",
        )

    # Guardrail passed
    log_agent_lifecycle(intent=intent, outcome="POLICY_PASSED")
    return PolicyEvaluationResult(
        allowed=True,
        policy_code="PASSED",
        reason="All safety and financial verification policies satisfied. Dispatch permitted.",
        remediation=None,
    )
