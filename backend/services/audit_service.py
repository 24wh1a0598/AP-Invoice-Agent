import logging
from datetime import datetime

from repositories.invoice_repo import InvoiceRepository


class AuditService:
    def __init__(self, repo: InvoiceRepository):
        self.repo = repo
        self.logger = logging.getLogger("audit_agent")

    def log_step(self, invoice_id: int, agent_name: str, decision: str, reasoning: str) -> None:
        payload = {
            "decision": decision,
            "reasoning": reasoning,
            "timestamp": datetime.utcnow().isoformat(),
        }
        self.logger.info(f"AUDIT | Invoice {invoice_id} | {agent_name} | {decision}")
        self.repo.create_audit_log(
            invoice_id=invoice_id,
            agent_name=agent_name,
            action=decision,
            details=payload,
        )
