"""Production FastAPI Application and Agent Service Endpoint.

Serves REST endpoints, Google Form webhooks, 1-Click zero-dashboard mobile HITL approval,
and Agent-to-Agent (A2A) protocol card for Gemini Enterprise / Vertex AI Agent Engine.
"""

from typing import Any, Dict, Optional
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from src.coordinator import (
    FormSubmissionPayload,
    ReconciliationCoordinator,
    WorkflowResult,
    coordinator,
)
from src.logging_tracer import log_agent_lifecycle, redact_pii

app = FastAPI(
    title="Autonomous Payment Reconciliation & Session Enrollment Agent",
    version="1.0.0",
    description="Automates Google Form payment approvals (UPI/PayPal) with 1-click zero-dashboard HITL mobile sign-off.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check() -> Dict[str, str]:
    """Health probe endpoint for Cloud Run, GKE, and Vertex AI Agent Engine."""
    return {"status": "healthy", "service": "payment-reconciliation-agent", "version": "1.0.0"}


@app.post("/process-form-submission", response_model=WorkflowResult)
async def handle_form_submission(payload: FormSubmissionPayload) -> WorkflowResult:
    """Webhook endpoint invoked when a user submits the course enrollment Google Form.

    Orchestrates:
    1. Fast Triage Validation
    2. Vision Receipt OCR Extraction
    3. Bank Statement Ledger Verification
    4. Guardrails & Zero-Dashboard HITL Escalation (for 3rd-party payer or mismatched UTR)
    5. Automatic Calendar & Email Dispatch upon verification
    """
    log_agent_lifecycle(
        intent=f"Handle incoming Google Form submission for {payload.applicant_name}",
        outcome=None,
        metadata={"form_entry_id": payload.form_entry_id, "session_level": payload.session_level},
    )

    result = await coordinator.process_form_submission(payload)
    return result


@app.get("/approve", response_class=HTMLResponse)
async def handle_1click_approval_get(
    token: str = Query(..., description="Cryptographically signed HMAC token from WhatsApp/Email"),
    action: str = Query("APPROVE", description="APPROVE or REJECT"),
    notes: Optional[str] = Query(None, description="Optional admin note"),
) -> HTMLResponse:
    """Zero-Dashboard 1-Click Human-in-the-Loop approval endpoint.

    Allows course administrator to approve or reject a payment with a single tap from
    WhatsApp or Email on their mobile phone without needing to log in to any dashboard.
    """
    result = await coordinator.resume_with_human_approval(token=token, action=action, admin_notes=notes)

    if result.status == "ENROLLED":
        color = "#10B981"
        badge = "APPROVED & DISPATCHED"
        msg = f"Payment approved for <strong>{result.applicant_name}</strong> (Level {result.session_level}). Calendar invite & confirmation email have been sent."
    elif result.status == "REJECTED":
        color = "#EF4444"
        badge = "REJECTED"
        msg = f"Enrollment for <strong>{result.applicant_name}</strong> was rejected."
    else:
        color = "#F59E0B"
        badge = "ERROR"
        msg = f"Could not process action: {result.message}"

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Payment Decision Recorded</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; background-color: #F3F4F6; }}
            .card {{ background: white; padding: 2rem; border-radius: 12px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); max-width: 450px; width: 90%; text-align: center; }}
            .badge {{ display: inline-block; padding: 0.35rem 0.75rem; border-radius: 9999px; font-size: 0.875rem; font-weight: 600; color: white; background-color: {color}; margin-bottom: 1rem; }}
            h2 {{ margin-top: 0; color: #111827; }}
            p {{ color: #4B5563; font-size: 1rem; line-height: 1.5; }}
            .footer {{ margin-top: 1.5rem; font-size: 0.75rem; color: #9CA3AF; }}
        </style>
    </head>
    <body>
        <div class="card">
            <span class="badge">{badge}</span>
            <h2>Action Processed</h2>
            <p>{msg}</p>
            <div class="footer">Autonomous Payment Reconciliation Agent • Google Cloud ADK</div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content, status_code=200)


@app.get("/a2a/app/.well-known/agent-card.json")
async def get_agent_card(request: Request) -> Dict[str, Any]:
    """Agent-to-Agent (A2A) protocol card for Gemini Enterprise / Vertex AI Agent Registry."""
    base_url = str(request.base_url).rstrip("/")
    return {
        "name": "Autonomous-Payment-Reconciliation-Agent",
        "description": "Verifies Google Form payments (UPI/PayPal screenshots) and manages session enrollments with 1-click mobile HITL approval.",
        "version": "1.0.0",
        "protocol": "A2A/1.0",
        "skills": [
            "payment-verification",
            "receipt-ocr-analysis",
            "bank-statement-reconciliation",
            "hitl-human-approval",
            "calendar-invite-dispatch",
        ],
        "endpoints": {
            "query": f"{base_url}/process-form-submission",
            "health": f"{base_url}/health",
        },
        "models": {
            "planning": "gemini-2.5-pro",
            "vision_extraction": "gemini-2.5-flash",
        },
    }
