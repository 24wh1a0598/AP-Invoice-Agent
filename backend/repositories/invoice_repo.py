from datetime import date, timedelta
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session
from models.models import Invoice, AuditLog, PurchaseOrder, Contract, InvoiceException


class InvoiceRepository:
    def __init__(self, db: Session):
        self.db = db

    # --- Purchase Order ---

    def get_po(self, po_number: str):
        return self.db.query(PurchaseOrder).filter(PurchaseOrder.po_number == po_number).first()

    # --- Contract ---

    def get_contract(self, contract_number: str):
        return self.db.query(Contract).filter(Contract.contract_number == contract_number).first()

    # --- Duplicate detection ---

    def find_by_invoice_number(
        self, invoice_number: str, exclude_id: int = 0
    ) -> Optional[Invoice]:
        """
        Returns the first persisted invoice whose invoice_number exactly matches,
        excluding the record currently being processed (exclude_id).

        PENDING-prefixed placeholders created by main.py before the pipeline runs
        will never match a real extracted invoice number, so they are naturally
        excluded without an extra filter.
        """
        return (
            self.db.query(Invoice)
            .filter(
                Invoice.invoice_number == invoice_number,
                Invoice.id != exclude_id,
            )
            .first()
        )

    def find_possible_duplicates(
        self,
        total_amount: float,
        invoice_date: Optional[date],
        exclude_id: int = 0,
        amount_tolerance: float = 0.01,
        date_window_days: int = 7,
    ) -> List[Invoice]:
        """
        Returns previously processed invoices with the same total amount that
        were *submitted* (created_at) within ±date_window_days of today.

        The date window is anchored on the current submission date (today),
        not the printed invoice_date from the document.  This is the correct
        semantic: "was a same-amount invoice submitted to this system recently?"
        which is the re-submission pattern we want to catch.

        invoice_date is accepted as a parameter so callers can pass it for
        future enrichment (e.g. comparing document dates directly once an
        invoice_date column is added to Invoice), but the query does not use it
        as the window anchor.

        Conservative by design:
        - Excludes the current PENDING record (exclude_id).
        - Excludes all PENDING-prefixed placeholders (not yet processed).
        - Only looks at amount + submission date window.
        - Does NOT flag REJECTED invoices as possible duplicates.
        """
        from models.models import InvoiceStatus
        import datetime as _dt

        today = _dt.date.today()
        date_from = today - timedelta(days=date_window_days)
        date_to = today + timedelta(days=date_window_days)

        q = (
            self.db.query(Invoice)
            .filter(
                Invoice.id != exclude_id,
                Invoice.invoice_number.notlike("PENDING-%"),
                Invoice.total_amount.between(
                    total_amount - amount_tolerance,
                    total_amount + amount_tolerance,
                ),
                # Exclude already-rejected invoices — they are not "in flight"
                Invoice.status != InvoiceStatus.REJECTED,
                # Window is on submission timestamp (created_at), not document date
                func.date(Invoice.created_at).between(
                    date_from.isoformat(), date_to.isoformat()
                ),
            )
        )

        return q.all()

    # --- Invoice ---

    def save_invoice(self, invoice: Invoice) -> Invoice:
        self.db.add(invoice)
        self.db.commit()
        self.db.refresh(invoice)
        return invoice

    def get_invoice(self, invoice_id: int) -> Invoice:
        return self.db.query(Invoice).filter(Invoice.id == invoice_id).first()

    def update_invoice_status(self, invoice_id: int, status) -> None:
        invoice = self.get_invoice(invoice_id)
        if invoice:
            invoice.status = status
            self.db.commit()

    # --- Exceptions ---

    def save_exceptions(self, invoice_id: int, exceptions: list) -> None:
        """
        Persists a list of exception dicts (each with 'type' and 'description' keys)
        as InvoiceException rows.
        """
        for exc in exceptions:
            record = InvoiceException(
                invoice_id=invoice_id,
                exception_type=exc.get("type", "UNKNOWN"),
                description=exc.get("description", ""),
            )
            self.db.add(record)
        self.db.commit()

    # --- Human review queue ---

    def get_review_queue(self, skip: int = 0, limit: int = 50) -> List[Invoice]:
        """
        Return invoices in REVIEW_REQUIRED status ordered oldest-first so AP
        clerks work through them in submission order.

        Excludes PENDING-prefixed placeholder rows created before the agent
        pipeline runs — those are never in REVIEW_REQUIRED status, but the
        filter is explicit for safety.
        """
        from models.models import InvoiceStatus

        return (
            self.db.query(Invoice)
            .filter(
                Invoice.status == InvoiceStatus.REVIEW_REQUIRED,
                Invoice.invoice_number.notlike("PENDING-%"),
            )
            .order_by(Invoice.created_at.asc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    # --- Vendor risk aggregates ---

    def get_vendor_by_id(self, vendor_id: int):
        """Return the Vendor ORM object for a given vendor_id, or None."""
        from models.models import Vendor
        return self.db.query(Vendor).filter(Vendor.id == vendor_id).first()

    def get_invoices_for_vendor(self, vendor_id: int) -> List[Invoice]:
        """
        Return all non-PENDING invoices whose PO links back to this vendor.

        Invoices are linked to vendors indirectly:
            Invoice.po_number → PurchaseOrder.po_number → PurchaseOrder.vendor_id

        Invoice.vendor_id is a nullable column that the current pipeline does not
        populate, so we cannot rely on it.  The join through PurchaseOrder is the
        only reliable path.  Invoices with no PO (MISSING_PO exception cases) do
        not appear here — they have no vendor link in the DB.
        """
        from models.models import PurchaseOrder, InvoiceStatus

        return (
            self.db.query(Invoice)
            .join(PurchaseOrder, Invoice.po_number == PurchaseOrder.po_number)
            .filter(
                PurchaseOrder.vendor_id == vendor_id,
                Invoice.invoice_number.notlike("PENDING-%"),
                Invoice.status != InvoiceStatus.PENDING,
            )
            .all()
        )

    def get_exception_counts_for_vendor(self, vendor_id: int) -> dict:
        """
        Return a dict mapping exception_type → count for all exceptions raised
        on invoices linked to this vendor.

        Uses a single SQL query with GROUP BY rather than loading every exception
        row into memory.
        """
        from models.models import PurchaseOrder

        rows = (
            self.db.query(
                InvoiceException.exception_type,
                func.count(InvoiceException.id).label("cnt"),
            )
            .join(Invoice, InvoiceException.invoice_id == Invoice.id)
            .join(PurchaseOrder, Invoice.po_number == PurchaseOrder.po_number)
            .filter(
                PurchaseOrder.vendor_id == vendor_id,
                Invoice.invoice_number.notlike("PENDING-%"),
            )
            .group_by(InvoiceException.exception_type)
            .all()
        )
        return {row.exception_type: row.cnt for row in rows}

    def get_recent_invoice_and_exception_counts(
        self, vendor_id: int, days: int = 30
    ) -> tuple[int, int]:
        """
        Return (invoice_count, exception_count) for invoices submitted in the
        last `days` days, for this vendor.

        Used for the recency signal in vendor risk scoring.
        """
        import datetime as _dt
        from models.models import PurchaseOrder

        since = _dt.datetime.utcnow() - _dt.timedelta(days=days)

        invoice_count = (
            self.db.query(func.count(Invoice.id))
            .join(PurchaseOrder, Invoice.po_number == PurchaseOrder.po_number)
            .filter(
                PurchaseOrder.vendor_id == vendor_id,
                Invoice.invoice_number.notlike("PENDING-%"),
                Invoice.created_at >= since,
            )
            .scalar()
            or 0
        )

        exception_count = (
            self.db.query(func.count(InvoiceException.id))
            .join(Invoice, InvoiceException.invoice_id == Invoice.id)
            .join(PurchaseOrder, Invoice.po_number == PurchaseOrder.po_number)
            .filter(
                PurchaseOrder.vendor_id == vendor_id,
                Invoice.invoice_number.notlike("PENDING-%"),
                Invoice.created_at >= since,
            )
            .scalar()
            or 0
        )

        return invoice_count, exception_count

    # --- Audit Log ---

    def create_audit_log(self, invoice_id: int, agent_name: str, action: str, details: dict = None) -> None:
        log = AuditLog(
            invoice_id=invoice_id,
            agent_name=agent_name,
            action=action,
            details=details or {},
        )
        self.db.add(log)
        self.db.commit()
