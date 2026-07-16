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
