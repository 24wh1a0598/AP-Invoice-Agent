"""
ApprovalService
===============
Business logic for the Human Review Workflow.

Responsibilities
----------------
- Validate that a requested state transition is legal.
- Apply the transition (mutate the Invoice row).
- Write an immutable audit log entry for every human action via AuditService.
- Return a structured result dict suitable for a JSON API response.

State transition rules
----------------------
Only invoices in REVIEW_REQUIRED status may receive human actions.
All other statuses are terminal or not yet ready for human review:

  PENDING          → not ready (still being processed)
  STRAIGHT_THROUGH → auto-approved by the agent; no human action required
  REVIEW_REQUIRED  → APPROVED  (via approve)
  REVIEW_REQUIRED  → REJECTED  (via reject)
  REVIEW_REQUIRED  → REVIEW_REQUIRED  (via request-info; status unchanged)
  APPROVED         → terminal  (cannot be re-approved or rejected)
  REJECTED         → terminal  (cannot be re-rejected or approved)
  PAID             → terminal  (cannot be touched)

User identity
-------------
The application does not yet have an authentication system.  User identity is
supplied by the caller as a plain string (e.g. "alice@corp.com") in the request
body and stored verbatim on the Invoice row (approved_by) and in the audit log.
This is a documented limitation; a real auth system would populate this from
a JWT claim or session token without requiring it in the body.

Audit log format
----------------
Every human action writes one AuditLog row via AuditService.log_step():

  agent_name : "human_review"
  action     : "APPROVED" | "REJECTED" | "INFO_REQUESTED"
  details    : {
      "decision"     : <action>,
      "reasoning"    : <human-readable summary>,
      "acted_by"     : <user identity string>,
      "prev_status"  : <previous InvoiceStatus value>,
      "new_status"   : <new InvoiceStatus value>,
      "reason"       : <rejection_reason or info message, if applicable>,
      "timestamp"    : <ISO 8601 UTC>,
  }
"""

from __future__ import annotations

import datetime
import logging
from typing import Optional

from models.models import Invoice, InvoiceStatus
from repositories.invoice_repo import InvoiceRepository
from services.audit_service import AuditService

logger = logging.getLogger("ap_agent.approval_service")

# Human-action audit agent name (consistent string stored in audit_logs.agent_name)
_HUMAN_AGENT = "human_review"

# The only status that accepts human actions
_REVIEWABLE = InvoiceStatus.REVIEW_REQUIRED

# Statuses that are already terminal and must not be mutated
_TERMINAL_STATUSES = {
    InvoiceStatus.APPROVED,
    InvoiceStatus.REJECTED,
    InvoiceStatus.PAID,
    InvoiceStatus.STRAIGHT_THROUGH,
}


class ApprovalError(Exception):
    """
    Raised when a state-transition is invalid.

    Attributes
    ----------
    message   : Human-readable explanation.
    http_code : Suggested HTTP status code for the API layer to use.
    """
    def __init__(self, message: str, http_code: int = 422) -> None:
        super().__init__(message)
        self.http_code = http_code


class ApprovalService:
    """
    Stateless service — receives an InvoiceRepository on construction so it
    can be injected with a test repository without patching globals.
    """

    def __init__(self, repo: InvoiceRepository) -> None:
        self._repo = repo
        self._audit = AuditService(repo)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def approve(
        self,
        invoice_id: int,
        acted_by: str,
    ) -> dict:
        """
        Approve a REVIEW_REQUIRED invoice.

        Parameters
        ----------
        invoice_id : DB primary key of the invoice.
        acted_by   : Identity of the approver (plain string, no auth yet).

        Returns
        -------
        dict with the updated invoice fields.

        Raises
        ------
        ApprovalError (http_code=404) if the invoice does not exist.
        ApprovalError (http_code=422) if the transition is illegal.
        """
        invoice = self._get_or_raise(invoice_id)
        self._assert_reviewable(invoice, action="approve")

        prev_status = invoice.status
        now = datetime.datetime.utcnow()

        invoice.status = InvoiceStatus.APPROVED
        invoice.approved_by = acted_by
        invoice.action_at = now
        self._repo.db.commit()

        reasoning = (
            f"Invoice approved by '{acted_by}'. "
            f"Previous status: {prev_status.value}."
        )
        self._audit.log_step(
            invoice_id=invoice_id,
            agent_name=_HUMAN_AGENT,
            decision="APPROVED",
            reasoning=reasoning,
        )
        # Patch the details payload to include structured fields
        self._patch_last_audit(
            invoice_id=invoice_id,
            extra={
                "acted_by": acted_by,
                "prev_status": prev_status.value,
                "new_status": InvoiceStatus.APPROVED.value,
                "reason": None,
            },
        )

        logger.info("Invoice %d approved by '%s'", invoice_id, acted_by)
        return self._invoice_response(invoice)

    def reject(
        self,
        invoice_id: int,
        acted_by: str,
        rejection_reason: str,
    ) -> dict:
        """
        Reject a REVIEW_REQUIRED invoice.

        Parameters
        ----------
        invoice_id       : DB primary key.
        acted_by         : Identity of the rejector.
        rejection_reason : Non-empty string explaining why the invoice is rejected.

        Raises
        ------
        ApprovalError (http_code=422) if rejection_reason is empty.
        ApprovalError (http_code=404) if invoice not found.
        ApprovalError (http_code=422) if transition is illegal.
        """
        if not rejection_reason or not rejection_reason.strip():
            raise ApprovalError("rejection_reason is required and must not be empty.")

        invoice = self._get_or_raise(invoice_id)
        self._assert_reviewable(invoice, action="reject")

        prev_status = invoice.status
        now = datetime.datetime.utcnow()

        invoice.status = InvoiceStatus.REJECTED
        invoice.approved_by = acted_by
        invoice.rejection_reason = rejection_reason.strip()
        invoice.action_at = now
        self._repo.db.commit()

        reasoning = (
            f"Invoice rejected by '{acted_by}'. "
            f"Reason: {rejection_reason.strip()}. "
            f"Previous status: {prev_status.value}."
        )
        self._audit.log_step(
            invoice_id=invoice_id,
            agent_name=_HUMAN_AGENT,
            decision="REJECTED",
            reasoning=reasoning,
        )
        self._patch_last_audit(
            invoice_id=invoice_id,
            extra={
                "acted_by": acted_by,
                "prev_status": prev_status.value,
                "new_status": InvoiceStatus.REJECTED.value,
                "reason": rejection_reason.strip(),
            },
        )

        logger.info(
            "Invoice %d rejected by '%s'. Reason: %s",
            invoice_id, acted_by, rejection_reason,
        )
        return self._invoice_response(invoice)

    def request_info(
        self,
        invoice_id: int,
        acted_by: str,
        message: str,
    ) -> dict:
        """
        Request additional information for a REVIEW_REQUIRED invoice.

        The invoice status remains REVIEW_REQUIRED — the invoice stays in the
        review queue. Only the audit log and action_at are updated.

        Parameters
        ----------
        invoice_id : DB primary key.
        acted_by   : Identity of the requestor.
        message    : Non-empty description of what information is needed.

        Raises
        ------
        ApprovalError (http_code=422) if message is empty.
        ApprovalError (http_code=404) if invoice not found.
        ApprovalError (http_code=422) if transition is illegal.
        """
        if not message or not message.strip():
            raise ApprovalError("message is required and must not be empty.")

        invoice = self._get_or_raise(invoice_id)
        self._assert_reviewable(invoice, action="request-info")

        now = datetime.datetime.utcnow()
        invoice.action_at = now
        self._repo.db.commit()

        reasoning = (
            f"Information requested by '{acted_by}'. "
            f"Message: {message.strip()}. "
            f"Invoice remains in REVIEW_REQUIRED status."
        )
        self._audit.log_step(
            invoice_id=invoice_id,
            agent_name=_HUMAN_AGENT,
            decision="INFO_REQUESTED",
            reasoning=reasoning,
        )
        self._patch_last_audit(
            invoice_id=invoice_id,
            extra={
                "acted_by": acted_by,
                "prev_status": InvoiceStatus.REVIEW_REQUIRED.value,
                "new_status": InvoiceStatus.REVIEW_REQUIRED.value,
                "reason": message.strip(),
            },
        )

        logger.info(
            "Info requested on invoice %d by '%s'",
            invoice_id, acted_by,
        )
        return {
            "invoice_id": invoice_id,
            "invoice_number": invoice.invoice_number,
            "status": invoice.status.value,
            "action": "INFO_REQUESTED",
            "message": message.strip(),
            "acted_by": acted_by,
            "action_at": now.isoformat(),
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_or_raise(self, invoice_id: int) -> Invoice:
        invoice = self._repo.get_invoice(invoice_id)
        if invoice is None:
            raise ApprovalError(
                f"Invoice {invoice_id} not found.", http_code=404
            )
        return invoice

    def _assert_reviewable(self, invoice: Invoice, action: str) -> None:
        """
        Raise ApprovalError if the invoice is not in a state that accepts
        human review actions.
        """
        current = invoice.status

        if current == _REVIEWABLE:
            return  # all good

        if current in _TERMINAL_STATUSES:
            raise ApprovalError(
                f"Cannot {action} invoice {invoice.id}: "
                f"current status is '{current.value}' which is terminal. "
                f"Only REVIEW_REQUIRED invoices can be actioned.",
                http_code=422,
            )

        # PENDING — still being processed by the agent
        raise ApprovalError(
            f"Cannot {action} invoice {invoice.id}: "
            f"current status is '{current.value}'. "
            f"Only REVIEW_REQUIRED invoices can be actioned.",
            http_code=422,
        )

    def _patch_last_audit(self, invoice_id: int, extra: dict) -> None:
        """
        Merge extra fields into the most recent audit log entry's details JSON.
        This enriches the standard AuditService payload with human-review-
        specific fields without changing AuditService itself.
        """
        from models.models import AuditLog
        log = (
            self._repo.db.query(AuditLog)
            .filter(AuditLog.invoice_id == invoice_id)
            .order_by(AuditLog.timestamp.desc())
            .first()
        )
        if log and isinstance(log.details, dict):
            merged = {**log.details, **extra}
            log.details = merged
            self._repo.db.commit()

    @staticmethod
    def _invoice_response(invoice: Invoice) -> dict:
        """Build the standard response dict for approve/reject."""
        return {
            "invoice_id": invoice.id,
            "invoice_number": invoice.invoice_number,
            "status": invoice.status.value,
            "approved_by": invoice.approved_by,
            "rejection_reason": invoice.rejection_reason,
            "action_at": invoice.action_at.isoformat() if invoice.action_at else None,
            "total_amount": invoice.total_amount,
            "currency": invoice.currency,
        }
