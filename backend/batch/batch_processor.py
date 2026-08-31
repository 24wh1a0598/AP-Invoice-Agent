"""
BatchProcessor
==============
Processes a list of SyntheticInvoice objects through the EXISTING
LangGraph invoice-processing pipeline and collects per-invoice results.

Key design decisions
--------------------
* Does NOT duplicate any validation/matching/duplicate-detection logic.
  It calls the same app_agent used by POST /upload-invoice.

* Uses the "pre-seeded extracted_data" short-circuit that already exists in
  extraction_node — passing populated extracted_data bypasses the LLM call
  entirely, making batch testing fast and deterministic.

* Seeds a fresh in-memory SQLite database (or accepts an injected session)
  with the reference Vendor, PO, and Contract required by the scenarios.

* Processes DUPLICATE scenario invoices last (the generator already does
  this) so their originals are in the DB when they run.

* Captures per-invoice processing time and any unexpected exceptions so
  the batch never crashes on a single bad invoice.

Reference DB setup
------------------
The processor seeds:
  Vendor   "Acme Supplies" / ACME-001
  PO       PO-BATCH-001  (Widget x10 @ $100 = $1,000)
  Contract CTR-BATCH-001 (max_amount $5,000)

These match the constants in invoice_generator.reference_data().
"""

from __future__ import annotations

import asyncio
import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from unittest.mock import MagicMock, patch

from database import Base
from models.models import Vendor, PurchaseOrder, Contract, Invoice, InvoiceStatus
from repositories.invoice_repo import InvoiceRepository
from agents.graph import app_agent
from batch.invoice_generator import (
    SyntheticInvoice,
    reference_data,
    SCENARIO_DUPLICATE,
)

logger = logging.getLogger("ap_agent.batch_processor")


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class BatchResult:
    """Per-invoice processing result."""
    invoice_id: str                   # SyntheticInvoice.invoice_id
    scenario: str
    ground_truth_decision: str        # "STRAIGHT_THROUGH" or "EXCEPTION"
    ground_truth_exception_type: Optional[str]

    actual_decision: Optional[str]    # final_state["status"] — None on crash
    actual_exceptions: List[Dict] = field(default_factory=list)
    processing_time_ms: float = 0.0
    success: bool = True              # False if the pipeline itself threw
    error_message: Optional[str] = None
    total_amount: float = 0.0


# ---------------------------------------------------------------------------
# Processor
# ---------------------------------------------------------------------------

class BatchProcessor:
    """
    Run a list of SyntheticInvoice objects through the existing agent pipeline.

    Parameters
    ----------
    db_session:
        Injected SQLAlchemy session (use this in tests with in-memory SQLite).
        If None, a fresh in-memory database is created for this batch.
    """

    def __init__(self, db_session: Optional[Session] = None) -> None:
        if db_session is not None:
            self._session = db_session
            self._owns_session = False
        else:
            engine = create_engine(
                "sqlite:///:memory:",
                connect_args={"check_same_thread": False},
            )
            Base.metadata.create_all(bind=engine)
            SessionLocal = sessionmaker(bind=engine)
            self._session = SessionLocal()
            self._owns_session = True

        self._seed_reference_data()

    def _seed_reference_data(self) -> None:
        """
        Seed all Vendors, POs, and Contracts used by synthetic batch scenarios.

        reference_data() returns {"vendors": [list of 3 vendor dicts]}.
        Each dict contains the keys: vendor_name, vendor_code, po_number,
        po_line_items, po_total, contract_number, contract_max_amount.

        Idempotent: existing records are detected by their unique codes/numbers
        and skipped — safe to call multiple times on the same session.
        """
        ref = reference_data()          # {"vendors": [...]}
        session = self._session

        for v in ref["vendors"]:
            # ---- Vendor ----
            vendor = session.query(Vendor).filter(
                Vendor.vendor_code == v["vendor_code"]
            ).first()
            if not vendor:
                vendor = Vendor(
                    name=v["vendor_name"],
                    vendor_code=v["vendor_code"],
                )
                session.add(vendor)
                session.flush()         # populate vendor.id before FK use

            # ---- Purchase Order ----
            po = session.query(PurchaseOrder).filter(
                PurchaseOrder.po_number == v["po_number"]
            ).first()
            if not po:
                po = PurchaseOrder(
                    po_number=v["po_number"],
                    vendor_id=vendor.id,
                    total_amount=v["po_total"],
                    status="OPEN",
                    line_items=v["po_line_items"],
                )
                session.add(po)

            # ---- Contract ----
            contract = session.query(Contract).filter(
                Contract.contract_number == v["contract_number"]
            ).first()
            if not contract:
                contract = Contract(
                    contract_number=v["contract_number"],
                    vendor_id=vendor.id,
                    max_amount=v["contract_max_amount"],
                )
                session.add(contract)

        session.commit()

    def process(self, invoices: List[SyntheticInvoice]) -> List[BatchResult]:
        """
        Process a list of SyntheticInvoice objects synchronously.

        Duplicate-scenario invoices must already be ordered last in the list
        (the generator guarantees this).  This ensures their originals are in
        the DB when the duplicate check runs.

        Returns a list of BatchResult, one per input invoice.
        """
        results: List[BatchResult] = []
        for synth in invoices:
            result = self._process_one(synth)
            results.append(result)
        return results

    def _process_one(self, synth: SyntheticInvoice) -> BatchResult:
        """Run one synthetic invoice through app_agent and capture the result."""
        t0 = time.monotonic()

        # Save a PENDING placeholder invoice row so the pipeline has a real DB id
        try:
            invoice = Invoice(
                invoice_number=f"PENDING-BATCH-{synth.invoice_id}",
                status=InvoiceStatus.PENDING,
                total_amount=0.0,
                tax_amount=0.0,
                currency="USD",
            )
            self._session.add(invoice)
            self._session.commit()
            self._session.refresh(invoice)
            invoice_id = invoice.id
        except Exception as exc:
            elapsed = (time.monotonic() - t0) * 1000
            logger.error("Failed to create PENDING record for %s: %s", synth.invoice_id, exc)
            return BatchResult(
                invoice_id=synth.invoice_id,
                scenario=synth.scenario,
                ground_truth_decision=synth.ground_truth.expected_decision,
                ground_truth_exception_type=synth.ground_truth.expected_exception_type,
                actual_decision=None,
                success=False,
                error_message=str(exc),
                processing_time_ms=elapsed,
                total_amount=synth.extracted_data.get("total_amount", 0.0) or 0.0,
            )

        initial_state = {
            "raw_text": f"[BATCH] Synthetic invoice {synth.invoice_id}",
            "extracted_data": synth.extracted_data,
            "exceptions": [],
            "status": "PENDING",
            "reasoning": ["[BATCH] Pre-seeded extraction data."],
            "invoice_id": invoice_id,
        }

        # Patch SessionLocal in agents.nodes to use OUR session so duplicate
        # detection and PO lookups hit the same in-memory DB.
        session_ref = self._session
        session_ref.close = lambda: None   # prevent nodes from closing our session

        mock_sl = MagicMock(return_value=session_ref)

        try:
            with patch("agents.nodes.SessionLocal", mock_sl):
                final_state = asyncio.run(
    app_agent.ainvoke(initial_state)
)

            actual_decision = final_state.get("status", "EXCEPTION")
            actual_exceptions = list(final_state.get("exceptions", []))

            # Persist the extracted invoice number and final status.
            # For DUPLICATE scenario invoices the extracted_data["invoice_number"]
            # is intentionally the same as the original.  Trying to update the
            # PENDING row to that name would hit the UNIQUE constraint.
            # We keep the PENDING placeholder name in that case — the important
            # thing is that the exceptions and decision are correctly recorded.
            total_amount = synth.extracted_data.get("total_amount", 0.0) or 0.0
            extracted_num = synth.extracted_data.get("invoice_number") or invoice.invoice_number

            # Guard: only use extracted_num if it doesn't already belong to a
            # different row (i.e. it's either our own PENDING row's number or
            # completely new).
            collision = (
                self._session.query(Invoice)
                .filter(
                    Invoice.invoice_number == extracted_num,
                    Invoice.id != invoice_id,
                )
                .first()
            )
            if collision:
                # Keep the PENDING placeholder to avoid a UNIQUE violation.
                extracted_num = invoice.invoice_number

            status_map = {
                "STRAIGHT_THROUGH": InvoiceStatus.STRAIGHT_THROUGH,
                "EXCEPTION": InvoiceStatus.REVIEW_REQUIRED,
                "EXTRACTION_FAILED": InvoiceStatus.REJECTED,
            }
            db_status = status_map.get(actual_decision, InvoiceStatus.REVIEW_REQUIRED)

            # Re-fetch and update after async run
            self._session.expire_all()
            inv_obj = self._session.query(Invoice).filter(Invoice.id == invoice_id).first()
            if inv_obj:
                inv_obj.invoice_number = extracted_num
                inv_obj.total_amount = total_amount
                inv_obj.status = db_status
                self._session.commit()

                # Persist exceptions
                repo = InvoiceRepository(self._session)
                if actual_exceptions:
                    repo.save_exceptions(invoice_id, actual_exceptions)

            elapsed = (time.monotonic() - t0) * 1000
            return BatchResult(
                invoice_id=synth.invoice_id,
                scenario=synth.scenario,
                ground_truth_decision=synth.ground_truth.expected_decision,
                ground_truth_exception_type=synth.ground_truth.expected_exception_type,
                actual_decision=actual_decision,
                actual_exceptions=actual_exceptions,
                processing_time_ms=round(elapsed, 2),
                success=True,
                total_amount=total_amount,
            )

        except Exception as exc:
            elapsed = (time.monotonic() - t0) * 1000
            logger.error("Pipeline error for %s: %s", synth.invoice_id, exc, exc_info=True)

            # Mark the DB record as rejected so it isn't left as PENDING
            try:
                self._session.expire_all()
                inv_obj = self._session.query(Invoice).filter(Invoice.id == invoice_id).first()
                if inv_obj:
                    inv_obj.status = InvoiceStatus.REJECTED
                    self._session.commit()
            except Exception:
                pass

            return BatchResult(
                invoice_id=synth.invoice_id,
                scenario=synth.scenario,
                ground_truth_decision=synth.ground_truth.expected_decision,
                ground_truth_exception_type=synth.ground_truth.expected_exception_type,
                actual_decision=None,
                success=False,
                error_message=str(exc),
                processing_time_ms=round(elapsed, 2),
                total_amount=synth.extracted_data.get("total_amount", 0.0) or 0.0,
            )

    def close(self) -> None:
        """Release the session if we created it."""
        if self._owns_session:
            try:
                self._session.close()
            except Exception:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
