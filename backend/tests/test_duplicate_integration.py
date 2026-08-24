"""
Integration tests — duplicate detection through the full LangGraph graph
=========================================================================
Three scenarios that exercise the complete pipeline (extract → validate →
duplicate_check → match → decide) with pre-seeded extracted_data so no
real LLM call is needed.

Each test uses an in-memory SQLite database seeded with the same PO + Contract
reference data as the eval suite, so PO/contract matching passes cleanly and
the only exceptions raised are (or are not) duplicates.

Run with:
    cd backend
    pytest tests/test_duplicate_integration.py -v
"""

import sys
import os
import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from unittest.mock import patch, MagicMock

from database import Base
from models.models import Vendor, PurchaseOrder, Contract, Invoice, InvoiceStatus
from repositories.invoice_repo import InvoiceRepository
from agents.graph import app_agent
from services.duplicate_service import EXACT_DUPLICATE_TYPE, POSSIBLE_DUPLICATE_TYPE


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_session():
    """
    Fresh in-memory SQLite with one Vendor, one PO, and one Contract seeded.
    This is the same baseline as the eval suite so PO/contract matching passes.
    """
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    vendor = Vendor(name="Dell Technologies", vendor_code="DELL-001")
    session.add(vendor)
    session.flush()

    po = PurchaseOrder(
        po_number="PO-999",
        vendor_id=vendor.id,
        total_amount=1000.00,
        status="OPEN",
        line_items=[
            {"description": "Laptop", "quantity": 10.0, "unit_price": 100.0, "total": 1000.0}
        ],
    )
    session.add(po)

    contract = Contract(
        contract_number="CTR-001",
        vendor_id=vendor.id,
        max_amount=15000.00,
    )
    session.add(contract)
    session.commit()

    yield session
    session.close()
    engine.dispose()


def _patch_session(session):
    """
    Returns a context-manager that makes SessionLocal always return the
    provided test session, and prevents it from being closed between node calls.
    """
    session.close = lambda: None  # keep session alive across node calls
    return patch("agents.nodes.SessionLocal", MagicMock(return_value=session))


# ---------------------------------------------------------------------------
# Base extracted data — a clean invoice that passes all other checks
# ---------------------------------------------------------------------------

def _clean_extracted(invoice_number: str = "INV-DUP-001") -> dict:
    return {
        "vendor_name": "Dell Technologies",
        "vendor_id": "DELL-001",
        "invoice_number": invoice_number,
        "invoice_date": "2024-06-01",
        "po_number": "PO-999",
        "contract_number": "CTR-001",
        "currency": "USD",
        "line_items": [
            {"description": "Laptop", "quantity": 10.0, "unit_price": 100.0, "total": 1000.0}
        ],
        "tax_amount": 0.0,
        "total_amount": 1000.0,
    }


# ---------------------------------------------------------------------------
# Scenario 1 — No duplicate: invoice processed cleanly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_duplicate_straight_through(db_session):
    """
    A first-time invoice with no prior records → STRAIGHT_THROUGH.
    No DUPLICATE_INVOICE or POSSIBLE_DUPLICATE exception should appear.
    """
    initial_state = {
        "raw_text": "Invoice INV-DUP-001",
        "extracted_data": _clean_extracted("INV-DUP-001"),
        "exceptions": [],
        "status": "PENDING",
        "reasoning": ["[TEST] pre-seeded"],
        "invoice_id": 0,
    }

    with _patch_session(db_session):
        result = await app_agent.ainvoke(initial_state)

    assert result["status"] == "STRAIGHT_THROUGH", (
        f"Expected STRAIGHT_THROUGH, got {result['status']}. "
        f"Exceptions: {result.get('exceptions')}"
    )
    exception_types = [e["type"] for e in result.get("exceptions", [])]
    assert EXACT_DUPLICATE_TYPE not in exception_types
    assert POSSIBLE_DUPLICATE_TYPE not in exception_types


# ---------------------------------------------------------------------------
# Scenario 2 — Exact duplicate: same invoice number resubmitted
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_exact_duplicate_raises_exception(db_session):
    """
    An invoice whose number already exists in the DB → EXCEPTION with
    DUPLICATE_INVOICE type.  The pipeline must NOT return STRAIGHT_THROUGH.
    """
    # Seed a prior completed invoice with the same number
    prior = Invoice(
        invoice_number="INV-DUP-EXACT",
        total_amount=1000.0,
        tax_amount=0.0,
        currency="USD",
        status=InvoiceStatus.STRAIGHT_THROUGH,
        created_at=datetime.datetime.utcnow() - datetime.timedelta(days=5),
    )
    db_session.add(prior)
    db_session.commit()

    initial_state = {
        "raw_text": "Invoice INV-DUP-EXACT",
        "extracted_data": _clean_extracted("INV-DUP-EXACT"),
        "exceptions": [],
        "status": "PENDING",
        "reasoning": ["[TEST] pre-seeded"],
        "invoice_id": 0,
    }

    with _patch_session(db_session):
        result = await app_agent.ainvoke(initial_state)

    assert result["status"] == "EXCEPTION", (
        f"Expected EXCEPTION for exact duplicate, got {result['status']}"
    )
    exception_types = [e["type"] for e in result["exceptions"]]
    assert EXACT_DUPLICATE_TYPE in exception_types, (
        f"Expected {EXACT_DUPLICATE_TYPE} in exceptions. Got: {result['exceptions']}"
    )
    # Confirm the prior invoice ID appears in the description
    dup_desc = next(
        e["description"] for e in result["exceptions"] if e["type"] == EXACT_DUPLICATE_TYPE
    )
    assert str(prior.id) in dup_desc, (
        f"Expected prior invoice ID {prior.id} in description: {dup_desc}"
    )
    assert "INV-DUP-EXACT" in dup_desc


# ---------------------------------------------------------------------------
# Scenario 3 — Possible duplicate: same amount + recent date, different number
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_possible_duplicate_raises_warning(db_session):
    """
    A different invoice number but same amount processed within the date window
    → EXCEPTION with POSSIBLE_DUPLICATE type.
    """
    # Seed a prior invoice: same amount, processed today (0 days ago)
    prior = Invoice(
        invoice_number="INV-PRIOR-POSS",
        total_amount=1000.0,
        tax_amount=0.0,
        currency="USD",
        status=InvoiceStatus.STRAIGHT_THROUGH,
        created_at=datetime.datetime.utcnow(),
    )
    db_session.add(prior)
    db_session.commit()

    # Incoming invoice has a DIFFERENT number but same amount and same date
    incoming = _clean_extracted("INV-NEW-POSS")  # different number
    incoming["total_amount"] = 1000.0
    incoming["invoice_date"] = "2024-06-01"

    initial_state = {
        "raw_text": "Invoice INV-NEW-POSS",
        "extracted_data": incoming,
        "exceptions": [],
        "status": "PENDING",
        "reasoning": ["[TEST] pre-seeded"],
        "invoice_id": 0,
    }

    with _patch_session(db_session):
        result = await app_agent.ainvoke(initial_state)

    assert result["status"] == "EXCEPTION", (
        f"Expected EXCEPTION for possible duplicate, got {result['status']}. "
        f"Exceptions: {result.get('exceptions')}"
    )
    exception_types = [e["type"] for e in result["exceptions"]]
    assert POSSIBLE_DUPLICATE_TYPE in exception_types, (
        f"Expected {POSSIBLE_DUPLICATE_TYPE} in exceptions. Got: {result['exceptions']}"
    )
    # Confirm both invoice numbers appear in the description
    poss_desc = next(
        e["description"] for e in result["exceptions"] if e["type"] == POSSIBLE_DUPLICATE_TYPE
    )
    assert "INV-NEW-POSS" in poss_desc
    assert "INV-PRIOR-POSS" in poss_desc


# ---------------------------------------------------------------------------
# Scenario 4 — Exact duplicate does NOT also add a possible duplicate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_exact_duplicate_does_not_also_flag_possible(db_session):
    """
    When an exact duplicate is found, the node returns early and does NOT
    additionally fire the possible duplicate check — we report one clear
    signal, not both.
    """
    prior = Invoice(
        invoice_number="INV-BOTH",
        total_amount=1000.0,
        tax_amount=0.0,
        currency="USD",
        status=InvoiceStatus.STRAIGHT_THROUGH,
        created_at=datetime.datetime.utcnow(),
    )
    db_session.add(prior)
    db_session.commit()

    initial_state = {
        "raw_text": "Invoice INV-BOTH",
        "extracted_data": _clean_extracted("INV-BOTH"),
        "exceptions": [],
        "status": "PENDING",
        "reasoning": ["[TEST] pre-seeded"],
        "invoice_id": 0,
    }

    with _patch_session(db_session):
        result = await app_agent.ainvoke(initial_state)

    exception_types = [e["type"] for e in result["exceptions"]]
    assert EXACT_DUPLICATE_TYPE in exception_types
    assert POSSIBLE_DUPLICATE_TYPE not in exception_types, (
        "Should not raise POSSIBLE_DUPLICATE when an exact match already exists"
    )


# ---------------------------------------------------------------------------
# Scenario 5 — Possible duplicate only; old invoice outside window → no flag
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_old_invoice_outside_window_no_flag(db_session):
    """
    An invoice with the same amount but processed 30 days ago (outside the
    ±7 day window) should NOT be flagged as a possible duplicate.
    """
    prior = Invoice(
        invoice_number="INV-OLD-OUTSIDE",
        total_amount=1000.0,
        tax_amount=0.0,
        currency="USD",
        status=InvoiceStatus.STRAIGHT_THROUGH,
        created_at=datetime.datetime.utcnow() - datetime.timedelta(days=30),
    )
    db_session.add(prior)
    db_session.commit()

    initial_state = {
        "raw_text": "Invoice INV-FRESH",
        "extracted_data": _clean_extracted("INV-FRESH"),
        "exceptions": [],
        "status": "PENDING",
        "reasoning": ["[TEST] pre-seeded"],
        "invoice_id": 0,
    }

    with _patch_session(db_session):
        result = await app_agent.ainvoke(initial_state)

    assert result["status"] == "STRAIGHT_THROUGH", (
        f"Expected STRAIGHT_THROUGH (old invoice outside window), "
        f"got {result['status']}. Exceptions: {result.get('exceptions')}"
    )
    exception_types = [e["type"] for e in result.get("exceptions", [])]
    assert POSSIBLE_DUPLICATE_TYPE not in exception_types
