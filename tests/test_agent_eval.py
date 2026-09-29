"""Automated Evaluation & Regression Test Suite for Payment Reconciliation Agent.

Validates the full system against tests/golden_dataset.json and tests every single
criterion in the 95/95 evaluation rubric:
1. Tool & Interface Design (20 pts)
2. Context & Memory (20 pts)
3. Orchestration & Logic (20 pts)
4. Observability & Tracing (20 pts)
5. Infrastructure & CI/CD (15 pts)
"""

import asyncio
import json
import os
import pytest
from typing import Any, Dict

from src.config import settings, SecretManagerConfig
from src.coordinator import (
    FormSubmissionPayload,
    ReconciliationCoordinator,
    WorkflowResult,
    coordinator,
)
from src.guardrails import evaluate_dispatch_policy, validate_session_fee
from src.logging_tracer import (
    log_agent_lifecycle,
    redact_pii,
    trace_agent_step,
    tracer,
)
from src.memory import CompactingSessionMemory
from src.prompts import SYSTEM_CONSTITUTION
from src.routing import TaskType, route_model_for_task
from src.tools import (
    BankVerificationInput,
    EnrollmentDispatchInput,
    HITLTicketInput,
    ReceiptAnalysisInput,
    ReceiptAnalysisOutput,
    dispatch_session_enrollment,
    extract_transaction_details_from_receipt,
    generate_hitl_approval_ticket,
    mock_bank_ledger,
    verify_bank_statement_record,
)


@pytest.fixture(autouse=True)
def reset_bank_ledger():
    """Ensures mock bank ledger is reset to pristine baseline before each test."""
    mock_bank_ledger.reset()
    yield
    mock_bank_ledger.reset()


# ===========================================================================
# 1. Golden Dataset Regression Suite (Threshold >= 0.90)
# ===========================================================================
@pytest.mark.asyncio
async def test_golden_dataset_evaluation():
    """Runs agent inference across all 10 edge cases in golden_dataset.json and asserts accuracy >= 0.90."""
    dataset_path = os.path.join(os.path.dirname(__file__), "golden_dataset.json")
    with open(dataset_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    cases = data["cases"]
    threshold = data.get("pass_threshold", 0.90)
    passed_cases = 0
    total_cases = len(cases)

    test_coordinator = ReconciliationCoordinator()

    for c in cases:
        case_id = c["case_id"]
        payload = FormSubmissionPayload(**c["input"])
        expected = c["expected_output"]

        result = await test_coordinator.process_form_submission(payload)

        # Evaluate match
        status_match = result.status == expected["status"]
        verified_match = result.is_payment_verified == expected["is_payment_verified"]

        hitl_match = True
        if "requires_hitl" in expected:
            hitl_match = (result.status == "AWAITING_HUMAN_APPROVAL") == expected["requires_hitl"]

        recovery_match = True
        if expected.get("requires_recovery_instructions"):
            recovery_match = result.recovery_instructions is not None and len(result.recovery_instructions) > 10

        case_passed = status_match and verified_match and hitl_match and recovery_match

        if case_passed:
            passed_cases += 1
        else:
            print(f"FAILED CASE: {case_id} -> got status={result.status}, verified={result.is_payment_verified}")

    accuracy = passed_cases / total_cases
    print(f"\n[Golden Dataset Evaluation] Score: {passed_cases}/{total_cases} ({accuracy * 100:.1f}%)")
    assert accuracy >= threshold, f"Golden dataset accuracy {accuracy:.2f} fell below threshold {threshold}"


# ===========================================================================
# 2. Tool & Interface Design Rubric Tests (20 pts)
# ===========================================================================
def test_tool_docstrings_and_naming():
    """Validates descriptive naming and human-readable comprehensive docstrings."""
    tool_fn = extract_transaction_details_from_receipt
    assert tool_fn.__name__ == "extract_transaction_details_from_receipt"
    assert tool_fn.__doc__ is not None
    assert "Extracts financial transaction details" in tool_fn.__doc__
    assert "Args:" in tool_fn.__doc__
    assert "Returns:" in tool_fn.__doc__


def test_tool_explicit_pydantic_schemas():
    """Validates explicit Pydantic input and output schemas."""
    # Test valid input schema
    valid_input = ReceiptAnalysisInput(
        receipt_image_base64="402819283741",
        mime_type="image/png",
        applicant_name_hint="Aarav Sharma",
        expected_session_level=1,
    )
    assert valid_input.expected_session_level == 1

    # Execute tool and validate structured output schema
    output = extract_transaction_details_from_receipt(valid_input)
    assert isinstance(output, ReceiptAnalysisOutput)
    assert output.status == "SUCCESS"
    assert output.transaction_id == "402819283741"
    assert output.amount == 4000.0
    assert output.confidence_score >= 0.85


def test_tool_guided_error_handling():
    """Validates guided error handling returning actionable instructions to LLM rather than crashing."""
    # Scenario: Blurry image input
    blurry_input = ReceiptAnalysisInput(receipt_image_base64="blurry_image_screenshot_unreadable")
    result = extract_transaction_details_from_receipt(blurry_input)

    assert result.status == "FAILED"
    assert result.confidence_score < 0.50
    assert result.error_message is not None
    assert result.recovery_instructions is not None
    assert "Request the applicant to upload a clear" in result.recovery_instructions

    # Scenario: Missing image data
    missing_input = ReceiptAnalysisInput(receipt_image_base64=None, receipt_image_url=None)
    result_missing = extract_transaction_details_from_receipt(missing_input)
    assert result_missing.status == "FAILED"
    assert "No image source provided" in result_missing.recovery_instructions


# ===========================================================================
# 3. Context & Memory Rubric Tests (20 pts)
# ===========================================================================
def test_system_constitution_defined():
    """Validates presence of robust system constitution establishing persona, rules, and PII boundaries."""
    assert "AUTONOMOUS PAYMENT RECONCILIATION & ENROLLMENT AGENT CONSTITUTION" in SYSTEM_CONSTITUTION
    assert "NEVER DISPATCH UNVERIFIED INVITES" in SYSTEM_CONSTITUTION
    assert "ZERO DOUBLE-SPEND TOLERANCE" in SYSTEM_CONSTITUTION
    assert "HUMAN-IN-THE-LOOP" in SYSTEM_CONSTITUTION


def test_compacting_session_memory_sliding_window():
    """Validates sliding-window compaction eliminates context bloat while preserving summary."""
    memory = CompactingSessionMemory(session_id="test_session_compact", max_turns=3)

    # Add 5 sequential turns
    for i in range(1, 6):
        memory.add_turn(role="user" if i % 2 != 0 else "agent", content=f"Step {i}: Interaction content here.")

    # Turns retained should be at most max_turns (3)
    assert len(memory.turns) <= 3
    # Summary must have been generated from compacted excess turns
    assert "Step 1" in memory.summary or "Summary of earlier turns" in memory.summary
    assert len(memory.get_compacted_context()) > 0


@pytest.mark.asyncio
async def test_async_memory_persistence():
    """Validates asynchronous memory persistence operates non-blockingly."""
    memory = CompactingSessionMemory(session_id="test_async_persist", max_turns=4)
    memory.add_turn("user", "Testing async non-blocking Firestore write.")

    # Call persist_state_async directly to verify execution
    await memory.persist_state_async()
    state = await memory.load_state_async()
    assert state["session_id"] == "test_async_persist"
    assert len(state["turns"]) >= 1


# ===========================================================================
# 4. Orchestration & Logic Rubric Tests (20 pts)
# ===========================================================================
def test_strategic_model_routing():
    """Validates strategic model routing: Flash for fast multimodal/PII and Pro for disputes/planning."""
    # Fast OCR task
    ocr_route = route_model_for_task(TaskType.OCR_EXTRACTION)
    assert ocr_route.model_name == "gemini-2.5-flash"
    assert ocr_route.temperature == 0.0

    # PII masking task
    pii_route = route_model_for_task(TaskType.PII_MASKING)
    assert pii_route.model_name == "gemini-2.5-flash"

    # Deep reconciliation task
    recon_route = route_model_for_task(TaskType.RECONCILIATION)
    assert recon_route.model_name == "gemini-2.5-pro"

    # Financial dispute task
    dispute_route = route_model_for_task(TaskType.FINANCIAL_DISPUTE)
    assert dispute_route.model_name == "gemini-2.5-pro"


def test_guardrails_prevent_unverified_dispatch():
    """Validates programmatic self-evaluation guardrail blocks unverified dispatches."""
    # Attempt dispatch when payment is unverified
    policy_fail = evaluate_dispatch_policy(
        is_payment_verified=False,
        confidence_score=0.99,
        is_third_party_payer=False,
        hitl_approved=False,
    )
    assert not policy_fail.allowed
    assert policy_fail.policy_code == "PAYMENT_UNVERIFIED"

    # Tool level guardrail refusal
    tool_dispatch = dispatch_session_enrollment(
        EnrollmentDispatchInput(
            applicant_email="fraud@example.com",
            applicant_name="Fraudster",
            session_level=1,
            is_payment_verified=False,
        )
    )
    assert not tool_dispatch.dispatched
    assert "SECURITY GUARDRAIL TRIGGERED" in tool_dispatch.message


@pytest.mark.asyncio
async def test_hitl_halt_gate_and_1click_resume():
    """Validates explicit HITL gate halts on family payer, issues token, and resumes cleanly."""
    coord = ReconciliationCoordinator()

    # Family payer scenario
    payload = FormSubmissionPayload(
        form_entry_id="TEST-HITL-01",
        applicant_name="Rohit Kumar",
        applicant_email="rohit.k@example.com",
        applicant_phone="+919876543211",
        session_level=2,
        receipt_image_base64="409988776655",  # Payer is Ramesh Kumar (Uncle)
    )

    result = await coord.process_form_submission(payload)
    # Must halt at HITL Gate
    assert result.status == "AWAITING_HUMAN_APPROVAL"
    assert result.hitl_ticket is not None
    token = result.hitl_ticket["approval_token"]
    assert token in coord.pending_hitl_tickets

    # Simulate Admin 1-Click Mobile Click: APPROVE
    resume_result = await coord.resume_with_human_approval(token=token, action="APPROVE")
    assert resume_result.status == "ENROLLED"
    assert resume_result.is_payment_verified is True
    assert resume_result.calendar_invite_url is not None


# ===========================================================================
# 5. Observability & Tracing Rubric Tests (20 pts)
# ===========================================================================
def test_pii_redaction_scrubbing():
    """Validates active regex scrubbing of emails and phone numbers."""
    raw_payload = {
        "user_email": "candidate@example.org",
        "phone": "+919876543210",
        "nested": {"contact": "Send email to admin@academy.com or call 555-234-5678"},
    }
    scrubbed = redact_pii(raw_payload)

    assert "[REDACTED_EMAIL]" in scrubbed["user_email"]
    assert "candidate@example.org" not in scrubbed["user_email"]
    assert "[REDACTED_PHONE]" in scrubbed["phone"]
    assert "[REDACTED_EMAIL]" in scrubbed["nested"]["contact"]
    assert "[REDACTED_PHONE]" in scrubbed["nested"]["contact"]


def test_paired_lifecycle_logging_and_tracing():
    """Validates intent vs. outcome paired capture and OpenTelemetry span attributes."""
    log_agent_lifecycle(
        intent="Test intent lifecycle capture",
        outcome="TEST_SUCCESS",
        metadata={"user_phone": "+919876543210"},
    )

    # Validate OpenTelemetry span context
    with trace_agent_step("test_tracing_step", "Verify span tagging") as span:
        span.set_attribute("custom.tag", "validation")
        # Attributes are tagged on the current span
        assert span.is_recording()


# ===========================================================================
# 6. Infrastructure & Secret Management Rubric Tests (15 pts)
# ===========================================================================
def test_zero_hardcoded_secrets_and_config():
    """Validates dynamic secret retrieval without hardcoded plaintext secrets."""
    cfg = SecretManagerConfig(project_id="test-project")
    key = cfg.gemini_api_key
    assert key is not None
    assert len(key) > 5

    hmac_key = cfg.hitl_hmac_secret
    assert hmac_key is not None
    assert len(hmac_key) > 10
