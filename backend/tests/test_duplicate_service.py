"""
Unit tests for DuplicateService
================================
Tests the service in isolation by injecting a real InvoiceRepository backed
by an in-memory SQLite database.  No LLM calls, no LangGraph graph.

Run with:
    cd backend
    pytest tests/test_duplicate_service.py -v
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import datetime
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.models import Invoice, InvoiceStatus
from repositories.invoice_repo import InvoiceRepository
from services.duplicate_service import (
    DuplicateService,
    EXACT_DUPLICATE_TYPE,
    POSSIBLE_DUPLICATE_TYPE,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_session():
    """Fresh in-memory SQLite for each test."""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    engine.dispose()


def _make_invoice(
    session,
    invoice_number: str,
    total_amount: float,
    status: InvoiceStatus = InvoiceStatus.STRAIGHT_THROUGH,
    days_ago: int = 0,
) -> Invoice:
    """Helper: persist a completed invoice and return the ORM object."""
    created = datetime.datetime.utcnow() - datetime.timedelta(days=days_ago)
    inv = Invoice(
        invoice_number=invoice_number,
        total_amount=total_amount,
        tax_amount=0.0,
        currency="USD",
        status=status,
        created_at=created,
    )
    session.add(inv)
    session.commit()
    session.refresh(inv)
    return inv


def _svc(session) -> DuplicateService:
    return DuplicateService(InvoiceRepository(session))


# ---------------------------------------------------------------------------
# check_exact_duplicate
# ---------------------------------------------------------------------------

class TestCheckExactDuplicate:

    def test_no_match_returns_none(self, db_session):
        """No existing invoice → no exception."""
        result = _svc(db_session).check_exact_duplicate("INV-999")
        assert result is None

    def test_exact_match_returns_exception(self, db_session):
        """Same invoice number → DUPLICATE_INVOICE exception."""
        existing = _make_invoice(db_session, "INV-001", 1000.0)
        result = _svc(db_session).check_exact_duplicate("INV-001")

        assert result is not None
        assert result["type"] == EXACT_DUPLICATE_TYPE
        assert str(existing.id) in result["description"]
        assert "INV-001" in result["description"]

    def test_exclude_id_prevents_self_match(self, db_session):
        """The PENDING record for the current upload must not match itself."""
        # Simulate: main.py saved a PENDING row with invoice_number="INV-002"
        # (this shouldn't happen with PENDING-UUID prefixes, but test the guard)
        inv = _make_invoice(db_session, "INV-002", 500.0, InvoiceStatus.PENDING)
        result = _svc(db_session).check_exact_duplicate("INV-002", current_invoice_id=inv.id)
        assert result is None

    def test_different_invoice_number_no_match(self, db_session):
        """Different invoice number should not trigger exact duplicate."""
        _make_invoice(db_session, "INV-100", 1000.0)
        result = _svc(db_session).check_exact_duplicate("INV-101")
        assert result is None

    def test_description_contains_existing_status(self, db_session):
        """Exception description should include the status of the existing invoice."""
        _make_invoice(db_session, "INV-ABC", 250.0, InvoiceStatus.REVIEW_REQUIRED)
        result = _svc(db_session).check_exact_duplicate("INV-ABC")
        assert result is not None
        assert "REVIEW_REQUIRED" in result["description"]

    def test_multiple_prior_invoices_still_detects(self, db_session):
        """Returns a match even when many other invoices exist."""
        for i in range(5):
            _make_invoice(db_session, f"INV-{i:03d}", float(i * 100))
        result = _svc(db_session).check_exact_duplicate("INV-003")
        assert result is not None
        assert result["type"] == EXACT_DUPLICATE_TYPE


# ---------------------------------------------------------------------------
# check_possible_duplicate
# ---------------------------------------------------------------------------

class TestCheckPossibleDuplicate:

    def test_no_existing_invoices_returns_none(self, db_session):
        """Empty DB → no possible duplicate."""
        from datetime import date
        result = _svc(db_session).check_possible_duplicate(
            invoice_number="INV-NEW",
            total_amount=500.0,
            invoice_date=date.today(),
        )
        assert result is None

    def test_same_amount_within_date_window_returns_exception(self, db_session):
        """Same amount, processed 3 days ago → POSSIBLE_DUPLICATE."""
        from datetime import date
        _make_invoice(db_session, "INV-OLD", 1500.0, days_ago=3)
        result = _svc(db_session).check_possible_duplicate(
            invoice_number="INV-NEW",
            total_amount=1500.0,
            invoice_date=date.today(),
        )
        assert result is not None
        assert result["type"] == POSSIBLE_DUPLICATE_TYPE
        assert "INV-NEW" in result["description"]
        assert "INV-OLD" in result["description"]

    def test_same_amount_outside_date_window_returns_none(self, db_session):
        """Same amount but processed 30 days ago (outside ±7 day window) → no flag."""
        from datetime import date
        _make_invoice(db_session, "INV-OLD-2", 1500.0, days_ago=30)
        result = _svc(db_session).check_possible_duplicate(
            invoice_number="INV-NEW-2",
            total_amount=1500.0,
            invoice_date=date.today(),
        )
        assert result is None

    def test_different_amount_within_date_window_returns_none(self, db_session):
        """Different amount, recent → no flag."""
        from datetime import date
        _make_invoice(db_session, "INV-DIFF", 999.0, days_ago=1)
        result = _svc(db_session).check_possible_duplicate(
            invoice_number="INV-NEW-3",
            total_amount=1000.0,  # $1 difference — outside $0.01 tolerance
            invoice_date=date.today(),
        )
        assert result is None

    def test_amount_within_tolerance_is_flagged(self, db_session):
        """Amount within $0.01 tolerance → POSSIBLE_DUPLICATE."""
        from datetime import date
        _make_invoice(db_session, "INV-CLOSE", 1000.00, days_ago=2)
        result = _svc(db_session).check_possible_duplicate(
            invoice_number="INV-NEW-4",
            total_amount=1000.005,  # within $0.01
            invoice_date=date.today(),
        )
        assert result is not None
        assert result["type"] == POSSIBLE_DUPLICATE_TYPE

    def test_rejected_invoice_is_not_flagged(self, db_session):
        """REJECTED invoices should not trigger possible duplicate."""
        from datetime import date
        _make_invoice(db_session, "INV-REJ", 750.0, InvoiceStatus.REJECTED, days_ago=1)
        result = _svc(db_session).check_possible_duplicate(
            invoice_number="INV-NEW-5",
            total_amount=750.0,
            invoice_date=date.today(),
        )
        assert result is None

    def test_pending_placeholder_is_not_flagged(self, db_session):
        """PENDING-prefixed placeholders created by main.py must not trigger."""
        from datetime import date
        _make_invoice(db_session, "PENDING-abc12345-invoice.pdf", 500.0, InvoiceStatus.PENDING, days_ago=0)
        result = _svc(db_session).check_possible_duplicate(
            invoice_number="INV-NEW-6",
            total_amount=500.0,
            invoice_date=date.today(),
        )
        assert result is None

    def test_exclude_id_prevents_self_match(self, db_session):
        """The current invoice's own PENDING row must not match itself."""
        from datetime import date
        inv = _make_invoice(db_session, "REAL-INV-99", 600.0, InvoiceStatus.PENDING, days_ago=0)
        result = _svc(db_session).check_possible_duplicate(
            invoice_number="REAL-INV-99",
            total_amount=600.0,
            invoice_date=date.today(),
            current_invoice_id=inv.id,
        )
        assert result is None

    def test_no_invoice_date_still_matches_on_amount(self, db_session):
        """When invoice_date is None, the date window is still applied using today's
        date as anchor. A same-amount invoice submitted today is within the window."""
        _make_invoice(db_session, "INV-NODATEOLD", 800.0, days_ago=0)
        result = _svc(db_session).check_possible_duplicate(
            invoice_number="INV-NODATENEW",
            total_amount=800.0,
            invoice_date=None,  # no document date — window still uses today
        )
        assert result is not None
        assert result["type"] == POSSIBLE_DUPLICATE_TYPE

    def test_description_format(self, db_session):
        """Description should mention both invoice numbers and the amount."""
        from datetime import date
        _make_invoice(db_session, "INV-FORMATTED", 2500.0, days_ago=1)
        result = _svc(db_session).check_possible_duplicate(
            invoice_number="INV-INCOMING",
            total_amount=2500.0,
            invoice_date=date.today(),
        )
        assert result is not None
        assert "2,500.00" in result["description"]
        assert "INV-INCOMING" in result["description"]
        assert "INV-FORMATTED" in result["description"]
