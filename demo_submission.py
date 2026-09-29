"""Live Simulation: Google Form Submission for Student prajp@google.com (Level 2 on Oct 5, 2026)

Admin: prajakta.pandharkar@gmail.com
"""

import asyncio
from src.coordinator import (
    FormSubmissionPayload,
    ReconciliationCoordinator,
)
from src.tools import mock_bank_ledger


async def run_demo():
    mock_bank_ledger.reset()
    coordinator = ReconciliationCoordinator()

    print("\n======================================================================")
    print("SCENARIO 1: Direct Payment by Student (Aarav / prajp@google.com)")
    print("Level 2 Session Fee: ₹8,000 INR | Event Date: Oct 5, 2026")
    print("======================================================================")

    # Let's add student's transaction into the bank ledger
    mock_bank_ledger.ledger["527819283741"] = {
        "amount": 8000.0,
        "currency": "INR",
        "payer_name": "Prajwal P",
        "payer_identifier": "prajp@upi",
        "timestamp": "2026-09-29T16:45:00Z",
        "used": False,
    }

    payload_direct = FormSubmissionPayload(
        form_entry_id="GOOGLE-FORM-ROW-201",
        applicant_name="Prajwal P",
        applicant_email="prajp@google.com",
        applicant_phone="+14155551234",
        session_level=2,
        receipt_image_base64="UPI Transaction Successful. UTR: 527819283741. Paid ₹8000.00 to Academy",
        claimed_transaction_id="527819283741",
    )

    result_direct = await coordinator.process_form_submission(payload_direct)

    print(f"Status:              {result_direct.status}")
    print(f"Payment Verified:    {result_direct.is_payment_verified}")
    print(f"Calendar Invite URL: {result_direct.calendar_invite_url}")
    print(f"Message:             {result_direct.message}")

    print("\n======================================================================")
    print("SCENARIO 2: Third-Party / Family Payment (Uncle Ramesh pays for student)")
    print("Requires Zero-Dashboard 1-Click HITL Sign-off by Admin prajakta.pandharkar@gmail.com")
    print("======================================================================")

    mock_bank_ledger.ledger["529988776655"] = {
        "amount": 8000.0,
        "currency": "INR",
        "payer_name": "Ramesh Kumar (Uncle)",
        "payer_identifier": "ramesh.uncle@okhdfcbank",
        "timestamp": "2026-09-29T16:46:00Z",
        "used": False,
    }

    payload_family = FormSubmissionPayload(
        form_entry_id="GOOGLE-FORM-ROW-202",
        applicant_name="Prajwal P",
        applicant_email="prajp@google.com",
        applicant_phone="+14155551234",
        session_level=2,
        receipt_image_base64="UPI Ref: 529988776655. Ramesh Kumar (Uncle). Amount: ₹8000",
        claimed_transaction_id="529988776655",
    )

    result_family = await coordinator.process_form_submission(payload_family)

    print(f"Status:              {result_family.status}")
    print(f"Payment Verified:    {result_family.is_payment_verified}")
    print(f"Message:             {result_family.message}")
    if result_family.hitl_ticket:
        ticket = result_family.hitl_ticket
        token = ticket["approval_token"]
        print(f"\nHITL Ticket ID:      {ticket['ticket_id']}")
        print(f"1-Click Approval URL:\n  {ticket['approval_url']}")
        print("\nWhatsApp / Email Alert sent to admin prajakta.pandharkar@gmail.com:")
        print("----------------------------------------------------------------------")
        print(ticket["whatsapp_message_text"])
        print("----------------------------------------------------------------------")

        # Step: Admin clicks 1-Click Approval link on phone!
        print("\n--> Admin prajakta.pandharkar@gmail.com taps 'APPROVE' on mobile link:")
        resume_result = await coordinator.resume_with_human_approval(token=token, action="APPROVE")
        print(f"Resumed Status:      {resume_result.status}")
        print(f"Calendar Invite URL: {resume_result.calendar_invite_url}")
        print(f"Message:             {resume_result.message}")


if __name__ == "__main__":
    asyncio.run(run_demo())
