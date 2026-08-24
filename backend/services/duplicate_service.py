"""
DuplicateService
================
Detects whether an incoming invoice is an exact or possible duplicate of a
previously processed invoice, using only information already present in the
database — no additional LLM calls are required.

Detection strategy
------------------
Exact duplicate
    Same invoice_number already exists in the invoices table (excluding the
    current PENDING placeholder row).  This is the highest-confidence signal:
    if a vendor resubmits the same invoice number we must flag it regardless of
    the amount or date.

Possible duplicate
    Different invoice_number, but same total_amount (within $0.01) *and* the
    existing invoice was processed within ±7 days of the incoming invoice_date.
    This catches re-submissions where the vendor changed only the invoice number
    while keeping everything else identical.

The service intentionally avoids matching on vendor_name because
Invoice.vendor_id is not yet populated by the pipeline (vendor identity
verification is a separate planned feature).  Matching on amount + date alone
is conservative enough to avoid false positives while still catching the common
re-submission pattern.

Both exception dicts returned by this service follow the same
  {"type": str, "description": str}
shape used throughout the pipeline, so they slot directly into the exceptions
list consumed by decision_node.
"""

import logging
from datetime import date
from typing import Optional

from repositories.invoice_repo import InvoiceRepository

logger = logging.getLogger("ap_agent.duplicate_service")

# Exception type constants — imported by nodes.py for consistency
EXACT_DUPLICATE_TYPE = "DUPLICATE_INVOICE"
POSSIBLE_DUPLICATE_TYPE = "POSSIBLE_DUPLICATE"


class DuplicateService:
    """
    Stateless service — receives a repository instance on construction so it
    can be injected with a mock in tests without patching SessionLocal.
    """

    def __init__(self, repo: InvoiceRepository) -> None:
        self._repo = repo

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_exact_duplicate(
        self,
        invoice_number: str,
        current_invoice_id: int = 0,
    ) -> Optional[dict]:
        """
        Returns a DUPLICATE_INVOICE exception dict if invoice_number already
        exists in the database, or None if no match is found.

        Parameters
        ----------
        invoice_number:
            The invoice number extracted from the incoming document.
        current_invoice_id:
            The database ID of the PENDING record created for this upload.
            It is excluded from the search so the record does not match itself.
        """
        existing = self._repo.find_by_invoice_number(
            invoice_number=invoice_number,
            exclude_id=current_invoice_id,
        )
        if existing is None:
            return None

        # Safely read status — may be an enum or a plain string depending on DB state
        try:
            existing_status = existing.status.value
        except AttributeError:
            existing_status = str(existing.status)

        description = (
            f"Invoice number '{invoice_number}' was already submitted "
            f"(existing record ID: {existing.id}, status: {existing_status}). "
            "Duplicate invoices must be reviewed manually."
        )
        logger.warning(
            "Exact duplicate detected: invoice_number='%s' "
            "new_id=%d existing_id=%d",
            invoice_number,
            current_invoice_id,
            existing.id,
        )
        return {"type": EXACT_DUPLICATE_TYPE, "description": description}

    def check_possible_duplicate(
        self,
        invoice_number: str,
        total_amount: float,
        invoice_date: Optional[date],
        current_invoice_id: int = 0,
        amount_tolerance: float = 0.01,
        date_window_days: int = 7,
    ) -> Optional[dict]:
        """
        Returns a POSSIBLE_DUPLICATE exception dict if another invoice with the
        same amount (within tolerance) was processed close to invoice_date, OR
        None if no candidate is found.

        This check is only meaningful when invoice_number is *different* from
        all existing records; call check_exact_duplicate first and skip this
        method if an exact match was already found.

        Parameters
        ----------
        invoice_number:
            Used only to exclude it from the description — the repo query
            already filters by amount + date, not by number.
        total_amount:
            Invoice total from the extracted data.
        invoice_date:
            Date extracted from the invoice document.  When None (unlikely after
            validation passes) the date window is not applied and only amount
            matching is used.
        current_invoice_id:
            Excluded from the DB search.
        amount_tolerance:
            Maximum allowed difference between the two invoice totals (default $0.01).
        date_window_days:
            Half-width of the date window in days (default ±7 days).
        """
        candidates = self._repo.find_possible_duplicates(
            total_amount=total_amount,
            invoice_date=invoice_date,
            exclude_id=current_invoice_id,
            amount_tolerance=amount_tolerance,
            date_window_days=date_window_days,
        )

        if not candidates:
            return None

        # Report the closest / most suspicious match — the first result
        match = candidates[0]
        try:
            match_status = match.status.value
        except AttributeError:
            match_status = str(match.status)

        description = (
            f"Possible duplicate detected: incoming invoice '{invoice_number}' "
            f"has the same total amount (${total_amount:,.2f}) as existing "
            f"invoice '{match.invoice_number}' "
            f"(ID: {match.id}, status: {match_status}). "
            "Please verify this is not a resubmission."
        )
        logger.warning(
            "Possible duplicate: invoice_number='%s' amount=%.2f "
            "matches existing id=%d invoice_number='%s'",
            invoice_number,
            total_amount,
            match.id,
            match.invoice_number,
        )
        return {"type": POSSIBLE_DUPLICATE_TYPE, "description": description}
