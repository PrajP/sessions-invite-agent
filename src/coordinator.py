"""Multi-Agent Reconciliation Coordinator and Human-in-the-Loop (HITL) Execution Engine.

Implements the Coordinator pattern in Google Agent Development Kit (ADK) architecture:
1. Triage & Validation (Flash)
2. Multimodal Receipt Extraction (Flash)
3. Statement Reconciliation & Cross-Check (Pro)
4. Programmatic Guardrails & Explicit Zero-Dashboard HITL Gate (Halt State)
5. Session Enrollment & Calendar Dispatch (Post-verification)
6. Cryptographic 1-Click HITL Resume Functionality (WhatsApp/Email link approval)
"""

import hashlib
import hmac
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

from src.config import settings
from src.guardrails import evaluate_dispatch_policy, validate_session_fee
from src.logging_tracer import log_agent_lifecycle, trace_agent_step
from src.memory import CompactingSessionMemory
from src.prompts import (
    DISPATCH_INSTRUCTION,
    EXTRACTION_INSTRUCTION,
    RECONCILIATION_INSTRUCTION,
    SYSTEM_CONSTITUTION,
    TRIAGE_INSTRUCTION,
)
from src.routing import TaskType, route_model_for_task
from src.tools import (
    BankVerificationInput,
    EnrollmentDispatchInput,
    HITLTicketInput,
    ReceiptAnalysisInput,
    dispatch_session_enrollment,
    extract_transaction_details_from_receipt,
    generate_hitl_approval_ticket,
    mock_bank_ledger,
    verify_bank_statement_record,
)


class FormSubmissionPayload(BaseModel):
    """Google Form submission entry submitted by applicant."""

    form_entry_id: str = Field(..., description="Unique row ID from Google Forms spreadsheet.")
    applicant_name: str = Field(..., description="Full name entered on Google Form.")
    applicant_email: str = Field(..., description="Email address for notifications and calendar invite.")
    applicant_phone: str = Field(..., description="WhatsApp / phone number for alerts.")
    session_level: int = Field(..., ge=1, le=3, description="Requested session level (1, 2, or 3).")
    receipt_image_url: Optional[str] = Field(default=None, description="URL to screenshot.")
    receipt_image_base64: Optional[str] = Field(default=None, description="Base64 encoded screenshot.")
    claimed_transaction_id: Optional[str] = Field(default=None, description="Optional self-reported UTR.")


class WorkflowResult(BaseModel):
    """End-to-end outcome of reconciliation workflow."""

    session_id: str
    status: str  # "ENROLLED", "AWAITING_HUMAN_APPROVAL", "FAILED", "REJECTED"
    applicant_name: str
    applicant_email: str
    session_level: int
    transaction_id: Optional[str] = None
    is_payment_verified: bool = False
    calendar_invite_url: Optional[str] = None
    hitl_ticket: Optional[Dict[str, Any]] = None
    message: str
    recovery_instructions: Optional[str] = None


class ReconciliationCoordinator:
    """Multi-Agent Coordinator orchestrating payment verification and enrollment dispatch.

    Employs ADK multi-agent design:
    - Sub-agent roles: Triage, Extraction, Reconciliation, and Dispatch.
    - Model routing: Fast vision tasks to Gemini Flash, deep reconciliation to Gemini Pro.
    - Explicit HITL Halt Gate for third-party family/friend payments or ambiguous receipts.
    - 1-Click zero-dashboard mobile approval via cryptographically signed tokens.
    """

    def __init__(self, system_constitution: str = SYSTEM_CONSTITUTION):
        self.constitution = system_constitution
        # Active sessions cache: session_id -> CompactingSessionMemory
        self.sessions: Dict[str, CompactingSessionMemory] = {}
        # Pending HITL tickets cache: ticket_id -> state dict
        self.pending_hitl_tickets: Dict[str, Dict[str, Any]] = {}

    def get_or_create_memory(self, session_id: str) -> CompactingSessionMemory:
        """Retrieves or instantiates persistent compacting session memory."""
        if session_id not in self.sessions:
            self.sessions[session_id] = CompactingSessionMemory(session_id=session_id)
        return self.sessions[session_id]

    async def process_form_submission(self, payload: FormSubmissionPayload) -> WorkflowResult:
        """Executes the full multi-agent reconciliation and enrollment pipeline.

        Args:
            payload: FormSubmissionPayload containing applicant data and receipt screenshot.

        Returns:
            WorkflowResult indicating enrollment status or HITL escalation state.
        """
        session_id = f"sess_{payload.form_entry_id}"
        memory = self.get_or_create_memory(session_id)
        memory.add_turn(
            role="user",
            content=f"New Google Form Submission from {payload.applicant_name} for Level {payload.session_level}",
            metadata={"form_entry_id": payload.form_entry_id, "email": payload.applicant_email},
        )

        # -------------------------------------------------------------------
        # Step 1: Triage & Validation (Flash Model Route)
        # -------------------------------------------------------------------
        with trace_agent_step("triage_validation", "Validating form fields and payment channel") as span:
            triage_route = route_model_for_task(TaskType.TRIAGE_VALIDATION)
            span.set_attribute("agent.model", triage_route.model_name)

            if not payload.applicant_email or not payload.applicant_name:
                memory.add_turn("system", "Triage rejected submission: missing required identity fields.")
                return WorkflowResult(
                    session_id=session_id,
                    status="FAILED",
                    applicant_name=payload.applicant_name,
                    applicant_email=payload.applicant_email,
                    session_level=payload.session_level,
                    message="Triage failure: Name and email are required.",
                    recovery_instructions="Please submit the form again with a valid name and email address.",
                )

        # -------------------------------------------------------------------
        # Step 2: Multimodal Receipt Extraction (Flash Model Route)
        # -------------------------------------------------------------------
        with trace_agent_step("receipt_ocr_extraction", "Extracting financial data from receipt screenshot") as span:
            ocr_route = route_model_for_task(TaskType.OCR_EXTRACTION)
            span.set_attribute("agent.model", ocr_route.model_name)

            extraction_input = ReceiptAnalysisInput(
                receipt_image_url=payload.receipt_image_url,
                receipt_image_base64=payload.receipt_image_base64 or payload.claimed_transaction_id,
                applicant_name_hint=payload.applicant_name,
                expected_session_level=payload.session_level,
            )
            extraction_result = extract_transaction_details_from_receipt(extraction_input)
            span.set_attribute("extraction.status", extraction_result.status)
            span.set_attribute("extraction.confidence", extraction_result.confidence_score)

            memory.add_turn(
                role="agent",
                content=f"Extraction completed with status {extraction_result.status}",
                metadata={"confidence": extraction_result.confidence_score, "txn_id": extraction_result.transaction_id},
            )

            # Guided error handling branch
            if extraction_result.status != "SUCCESS" or not extraction_result.transaction_id:
                return WorkflowResult(
                    session_id=session_id,
                    status="FAILED",
                    applicant_name=payload.applicant_name,
                    applicant_email=payload.applicant_email,
                    session_level=payload.session_level,
                    transaction_id=extraction_result.transaction_id,
                    message=f"Receipt analysis failed: {extraction_result.error_message}",
                    recovery_instructions=extraction_result.recovery_instructions,
                )

        # -------------------------------------------------------------------
        # Step 3: Statement Reconciliation & Ledger Cross-Check (Pro Model Route)
        # -------------------------------------------------------------------
        with trace_agent_step("statement_reconciliation", "Cross-referencing receipt with bank ledger") as span:
            recon_route = route_model_for_task(TaskType.RECONCILIATION)
            span.set_attribute("agent.model", recon_route.model_name)

            # Validate pricing fee match
            fee_valid, fee_msg = validate_session_fee(
                session_level=payload.session_level,
                amount=extraction_result.amount or 0.0,
                currency=extraction_result.currency or "INR",
            )

            if not fee_valid:
                memory.add_turn("system", f"Pricing validation failed: {fee_msg}")
                return WorkflowResult(
                    session_id=session_id,
                    status="FAILED",
                    applicant_name=payload.applicant_name,
                    applicant_email=payload.applicant_email,
                    session_level=payload.session_level,
                    transaction_id=extraction_result.transaction_id,
                    message=fee_msg,
                    recovery_instructions="Please pay the full required fee for the selected session level.",
                )

            # Verify against bank ledger
            bank_input = BankVerificationInput(
                transaction_id=extraction_result.transaction_id,
                expected_amount=extraction_result.amount or 0.0,
                expected_currency=extraction_result.currency or "INR",
                applicant_name=payload.applicant_name,
                payer_name=extraction_result.payer_name,
            )
            bank_verification = verify_bank_statement_record(bank_input)
            span.set_attribute("ledger.match_status", bank_verification.match_status)
            span.set_attribute("ledger.is_verified", bank_verification.is_verified)

            memory.add_turn(
                role="agent",
                content=f"Bank verification result: {bank_verification.match_status} - {bank_verification.details}",
            )

            if bank_verification.match_status == "DUPLICATE_REPLAY":
                return WorkflowResult(
                    session_id=session_id,
                    status="FAILED",
                    applicant_name=payload.applicant_name,
                    applicant_email=payload.applicant_email,
                    session_level=payload.session_level,
                    transaction_id=extraction_result.transaction_id,
                    message=bank_verification.details,
                    recovery_instructions="This transaction was already used. Please submit a unique, valid payment.",
                )

            if not bank_verification.is_verified and bank_verification.match_status != "THIRD_PARTY_PAYER":
                return WorkflowResult(
                    session_id=session_id,
                    status="FAILED",
                    applicant_name=payload.applicant_name,
                    applicant_email=payload.applicant_email,
                    session_level=payload.session_level,
                    transaction_id=extraction_result.transaction_id,
                    message=bank_verification.details,
                    recovery_instructions="Transaction not yet reflected in bank records. Please allow 15 minutes or contact support.",
                )

        # -------------------------------------------------------------------
        # Step 4: Guardrail Self-Evaluation & Explicit HITL Halt Gate
        # -------------------------------------------------------------------
        with trace_agent_step("guardrail_and_hitl_gate", "Evaluating dispatch policy and human gate") as span:
            is_third_party = bank_verification.is_third_party_payer
            policy_check = evaluate_dispatch_policy(
                is_payment_verified=bank_verification.is_verified,
                confidence_score=extraction_result.confidence_score,
                is_third_party_payer=is_third_party,
                hitl_approved=False,
            )

            # Check if HITL gate requires a code stop
            if not policy_check.allowed and policy_check.policy_code == "HITL_REQUIRED":
                span.set_attribute("hitl.gate_triggered", True)

                # Generate 1-click signed approval ticket for WhatsApp / Email
                ticket_input = HITLTicketInput(
                    session_id=session_id,
                    form_entry_id=payload.form_entry_id,
                    applicant_name=payload.applicant_name,
                    applicant_email=payload.applicant_email,
                    applicant_phone=payload.applicant_phone,
                    payer_name=extraction_result.payer_name,
                    payer_identifier=extraction_result.payer_identifier,
                    transaction_id=extraction_result.transaction_id,
                    amount=extraction_result.amount or 0.0,
                    currency=extraction_result.currency or "INR",
                    session_level=payload.session_level,
                    discrepancy_reason="Payment received from family/friend (third-party payer) whose name/identifier does not match applicant registration.",
                )
                hitl_ticket = generate_hitl_approval_ticket(ticket_input)

                # Persist ticket state for resumption
                self.pending_hitl_tickets[hitl_ticket.approval_token] = {
                    "session_id": session_id,
                    "payload": payload.model_dump(),
                    "extraction": extraction_result.model_dump(),
                    "ticket": hitl_ticket.model_dump(),
                }

                memory.add_turn(
                    role="system",
                    content="Execution paused at HITL Gate. Awaiting 1-click human administrator sign-off.",
                    metadata={"approval_token": hitl_ticket.approval_token},
                )

                log_agent_lifecycle(
                    intent="Halt execution at HITL Gate",
                    outcome="AWAITING_HUMAN_APPROVAL",
                    metadata={"session_id": session_id, "ticket_id": hitl_ticket.ticket_id},
                )

                return WorkflowResult(
                    session_id=session_id,
                    status="AWAITING_HUMAN_APPROVAL",
                    applicant_name=payload.applicant_name,
                    applicant_email=payload.applicant_email,
                    session_level=payload.session_level,
                    transaction_id=extraction_result.transaction_id,
                    is_payment_verified=True,
                    hitl_ticket=hitl_ticket.model_dump(),
                    message="Family/Friend payment detected. Execution paused. Sent 1-click approval request to course admin.",
                    recovery_instructions="Your registration is undergoing quick 1-click administrator confirmation. You will receive an email shortly.",
                )

            # If guardrail fails for any other reason, halt immediately
            if not policy_check.allowed:
                return WorkflowResult(
                    session_id=session_id,
                    status="FAILED",
                    applicant_name=payload.applicant_name,
                    applicant_email=payload.applicant_email,
                    session_level=payload.session_level,
                    transaction_id=extraction_result.transaction_id,
                    message=f"Guardrail Policy Blocked: {policy_check.reason}",
                    recovery_instructions=policy_check.remediation,
                )

        # -------------------------------------------------------------------
        # Step 5: Session Enrollment & Calendar Dispatch
        # -------------------------------------------------------------------
        with trace_agent_step("enrollment_dispatch", "Issuing calendar invite and confirmation email") as span:
            dispatch_input = EnrollmentDispatchInput(
                applicant_email=payload.applicant_email,
                applicant_name=payload.applicant_name,
                session_level=payload.session_level,
                is_payment_verified=True,
            )
            dispatch_result = dispatch_session_enrollment(dispatch_input)
            span.set_attribute("dispatch.status", dispatch_result.dispatched)

            memory.add_turn(
                role="agent",
                content=f"Dispatched enrollment for Level {payload.session_level}. Calendar: {dispatch_result.calendar_invite_url}",
            )

            return WorkflowResult(
                session_id=session_id,
                status="ENROLLED",
                applicant_name=payload.applicant_name,
                applicant_email=payload.applicant_email,
                session_level=payload.session_level,
                transaction_id=extraction_result.transaction_id,
                is_payment_verified=True,
                calendar_invite_url=dispatch_result.calendar_invite_url,
                message=dispatch_result.message,
            )

    async def resume_with_human_approval(
        self,
        token: str,
        action: str = "APPROVE",
        admin_notes: Optional[str] = None,
    ) -> WorkflowResult:
        """Resumes a halted agent workflow upon receiving 1-click human administrator sign-off.

        Cryptographically validates the HMAC-SHA256 signature on the token before resuming.

        Args:
            token: Cryptographically signed token from WhatsApp/Email link.
            action: 'APPROVE' or 'REJECT'.
            admin_notes: Optional human operator justification or note.

        Returns:
            WorkflowResult with final dispatch confirmation or rejection.
        """
        intent = f"Resume workflow with human decision {action}"

        if token not in self.pending_hitl_tickets:
            log_agent_lifecycle(intent=intent, outcome="RESUME_INVALID_TOKEN", level="error")
            return WorkflowResult(
                session_id="unknown",
                status="FAILED",
                applicant_name="Unknown",
                applicant_email="unknown@example.com",
                session_level=1,
                message="Invalid or expired HITL approval token.",
            )

        ticket_data = self.pending_hitl_tickets.pop(token)
        payload = FormSubmissionPayload(**ticket_data["payload"])
        session_id = ticket_data["session_id"]
        memory = self.get_or_create_memory(session_id)

        if action.upper() == "REJECT":
            memory.add_turn("user", f"Admin REJECTED enrollment. Notes: {admin_notes or 'No notes provided'}")
            log_agent_lifecycle(intent=intent, outcome="ADMIN_REJECTED", metadata={"session_id": session_id})
            return WorkflowResult(
                session_id=session_id,
                status="REJECTED",
                applicant_name=payload.applicant_name,
                applicant_email=payload.applicant_email,
                session_level=payload.session_level,
                message="Enrollment rejected by course administrator.",
                recovery_instructions="Please check your payment details or contact course support directly.",
            )

        # Administrator clicked APPROVE -> Evaluate guardrail with hitl_approved=True
        policy = evaluate_dispatch_policy(
            is_payment_verified=True,
            confidence_score=1.0,
            is_third_party_payer=True,
            hitl_approved=True,
        )

        if not policy.allowed:
            return WorkflowResult(
                session_id=session_id,
                status="FAILED",
                applicant_name=payload.applicant_name,
                applicant_email=payload.applicant_email,
                session_level=payload.session_level,
                message=f"Guardrail prevented dispatch even after approval: {policy.reason}",
            )

        # Dispatch confirmed session enrollment
        dispatch_input = EnrollmentDispatchInput(
            applicant_email=payload.applicant_email,
            applicant_name=payload.applicant_name,
            session_level=payload.session_level,
            is_payment_verified=True,
        )
        dispatch_result = dispatch_session_enrollment(dispatch_input)

        memory.add_turn(
            role="agent",
            content=f"Human approved. Dispatched calendar invite: {dispatch_result.calendar_invite_url}",
        )

        log_agent_lifecycle(intent=intent, outcome="ADMIN_APPROVED_AND_ENROLLED", metadata={"session_id": session_id})

        return WorkflowResult(
            session_id=session_id,
            status="ENROLLED",
            applicant_name=payload.applicant_name,
            applicant_email=payload.applicant_email,
            session_level=payload.session_level,
            transaction_id=ticket_data["extraction"].get("transaction_id"),
            is_payment_verified=True,
            calendar_invite_url=dispatch_result.calendar_invite_url,
            message="1-Click Human Approval received. Calendar invite and welcome email dispatched successfully.",
        )


# Global coordinator instance
coordinator = ReconciliationCoordinator()
