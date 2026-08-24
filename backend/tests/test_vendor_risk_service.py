"""
Unit tests for VendorRiskService
==================================
Tests the service in isolation using an in-memory SQLite database.
No LLM calls, no LangGraph graph, no Groq API.

The tests seed a Vendor, PurchaseOrder, and a variable set of Invoices +
InvoiceExceptions directly — the same approach used in test_duplicate_service.py.

Run with:
    cd backend
    pytest tests/test_vendor_risk_service.py -v
"""

import sys
import os
import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.models import Vendor, PurchaseOrder, Invoice, InvoiceException, InvoiceStatus
from repositories.invoice_repo import InvoiceRepository
from services.vendor_risk_service import VendorRiskService, VendorRiskReport
from services.duplicate_service import EXACT_DUPLICATE_TYPE, POSSIBLE_DUPLICATE_TYPE


# ---------------------------------------------------------------------------
# Fixtures and helpers
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


def _seed_vendor(session, name="Acme Corp", code="ACME-001") -> Vendor:
    """Create and persist a Vendor; return the ORM object."""
    v = Vendor(name=name, vendor_code=code)
    session.add(v)
    session.flush()
    return v


def _seed_po(session, vendor_id: int, po_number: str = "PO-TEST-001") -> PurchaseOrder:
    """Create and persist a PurchaseOrder for a vendor."""
    po = PurchaseOrder(
        po_number=po_number,
        vendor_id=vendor_id,
        total_amount=1000.0,
        status="OPEN",
        line_items=[
            {"description": "Widget", "quantity": 10.0, "unit_price": 100.0, "total": 1000.0}
        ],
    )
    session.add(po)
    session.flush()
    return po


def _add_invoice(
    session,
    po_number: str,
    invoice_number: str,
    status: InvoiceStatus = InvoiceStatus.STRAIGHT_THROUGH,
    total_amount: float = 1000.0,
    days_ago: int = 5,
) -> Invoice:
    """Create and persist an Invoice linked to a PO (and thus to a vendor)."""
    created = datetime.datetime.utcnow() - datetime.timedelta(days=days_ago)
    inv = Invoice(
        invoice_number=invoice_number,
        po_number=po_number,
        total_amount=total_amount,
        tax_amount=0.0,
        currency="USD",
        status=status,
        created_at=created,
    )
    session.add(inv)
    session.flush()
    return inv


def _add_exception(
    session, invoice_id: int, exc_type: str, description: str = "test"
) -> InvoiceException:
    """Attach a single exception row to an invoice."""
    exc = InvoiceException(
        invoice_id=invoice_id,
        exception_type=exc_type,
        description=description,
    )
    session.add(exc)
    session.flush()
    return exc


def _svc(session) -> VendorRiskService:
    return VendorRiskService(InvoiceRepository(session))


# ---------------------------------------------------------------------------
# Test: unknown vendor
# ---------------------------------------------------------------------------

class TestUnknownVendor:

    def test_raises_value_error_for_missing_vendor(self, db_session):
        with pytest.raises(ValueError, match="9999"):
            _svc(db_session).calculate(vendor_id=9999)


# ---------------------------------------------------------------------------
# Test: vendor with no invoices
# ---------------------------------------------------------------------------

class TestNoInvoices:

    def test_vendor_with_no_invoices_returns_zero_score(self, db_session):
        vendor = _seed_vendor(db_session)
        db_session.commit()
        report = _svc(db_session).calculate(vendor.id)

        assert isinstance(report, VendorRiskReport)
        assert report.risk_score == 0
        assert report.risk_level == "LOW"
        assert report.total_invoices == 0
        assert report.total_exceptions == 0
        assert report.exception_rate == 0.0
        assert report.duplicate_count == 0
        assert report.po_mismatch_count == 0
        assert report.contract_violation_count == 0

    def test_no_invoices_includes_insufficient_data_factor(self, db_session):
        vendor = _seed_vendor(db_session)
        db_session.commit()
        report = _svc(db_session).calculate(vendor.id)

        assert any("insufficient data" in f.lower() for f in report.risk_factors), (
            f"Expected 'insufficient data' factor. Got: {report.risk_factors}"
        )

    def test_report_contains_correct_vendor_metadata(self, db_session):
        vendor = _seed_vendor(db_session, name="Test Vendor", code="TEST-42")
        db_session.commit()
        report = _svc(db_session).calculate(vendor.id)

        assert report.vendor_id == vendor.id
        assert report.vendor_name == "Test Vendor"
        assert report.vendor_code == "TEST-42"


# ---------------------------------------------------------------------------
# Test: vendor with clean invoices (no exceptions)
# ---------------------------------------------------------------------------

class TestCleanVendor:

    def test_clean_vendor_has_low_risk(self, db_session):
        vendor = _seed_vendor(db_session)
        po = _seed_po(db_session, vendor.id)
        db_session.commit()

        for i in range(5):
            _add_invoice(db_session, po.po_number, f"INV-CLEAN-{i:03d}")
        db_session.commit()

        report = _svc(db_session).calculate(vendor.id)

        assert report.risk_score == 0
        assert report.risk_level == "LOW"
        assert report.total_invoices == 5
        assert report.total_exceptions == 0
        assert report.exception_rate == 0.0
        assert report.risk_factors == []  # no active factors, only the clean invoices

    def test_clean_vendor_score_breakdown_is_empty(self, db_session):
        vendor = _seed_vendor(db_session)
        po = _seed_po(db_session, vendor.id)
        db_session.commit()

        for i in range(3):
            _add_invoice(db_session, po.po_number, f"INV-C-{i}")
        db_session.commit()

        report = _svc(db_session).calculate(vendor.id)
        assert report.score_breakdown == []


# ---------------------------------------------------------------------------
# Test: exception rate signal (Signal 1)
# ---------------------------------------------------------------------------

class TestExceptionRateSignal:

    def test_low_exception_rate_below_threshold(self, db_session):
        """5% exception rate → 0 pts (below 10% threshold)."""
        vendor = _seed_vendor(db_session)
        po = _seed_po(db_session, vendor.id)
        db_session.commit()

        for i in range(20):
            inv = _add_invoice(db_session, po.po_number, f"INV-RATE-{i:03d}")
            if i == 0:  # 1 exception out of 20 = 5%
                _add_exception(db_session, inv.id, "UNKNOWN_PO")
        db_session.commit()

        report = _svc(db_session).calculate(vendor.id)
        pts = sum(b["points"] for b in report.score_breakdown if b["signal"] == "exception_rate")
        assert pts == 0

    def test_exception_rate_10_percent_tier(self, db_session):
        """2 exceptions out of 10 = 20% → 25 pts tier."""
        vendor = _seed_vendor(db_session)
        po = _seed_po(db_session, vendor.id)
        db_session.commit()

        for i in range(10):
            inv = _add_invoice(db_session, po.po_number, f"INV-T10-{i:03d}")
            if i < 2:
                _add_exception(db_session, inv.id, "UNKNOWN_PO")
        db_session.commit()

        report = _svc(db_session).calculate(vendor.id)
        # 2/10 = 20% → ≥10% but <25% → 15 pts
        pts = sum(b["points"] for b in report.score_breakdown if b["signal"] == "exception_rate")
        assert pts == 15

    def test_exception_rate_50_percent_tier(self, db_session):
        """5 exceptions out of 10 = 50% → 40 pts."""
        vendor = _seed_vendor(db_session)
        po = _seed_po(db_session, vendor.id)
        db_session.commit()

        for i in range(10):
            inv = _add_invoice(db_session, po.po_number, f"INV-T50-{i:03d}")
            if i < 5:
                _add_exception(db_session, inv.id, "UNKNOWN_PO")
        db_session.commit()

        report = _svc(db_session).calculate(vendor.id)
        pts = sum(b["points"] for b in report.score_breakdown if b["signal"] == "exception_rate")
        assert pts == 40


# ---------------------------------------------------------------------------
# Test: duplicate exceptions (Signal 2)
# ---------------------------------------------------------------------------

class TestDuplicateSignal:

    def test_one_duplicate_adds_10_pts(self, db_session):
        vendor = _seed_vendor(db_session)
        po = _seed_po(db_session, vendor.id)
        db_session.commit()

        inv = _add_invoice(db_session, po.po_number, "INV-DUP-1",
                           status=InvoiceStatus.REVIEW_REQUIRED)
        _add_exception(db_session, inv.id, EXACT_DUPLICATE_TYPE, "duplicate")
        db_session.commit()

        report = _svc(db_session).calculate(vendor.id)
        pts = sum(b["points"] for b in report.score_breakdown if b["signal"] == "duplicate_history")
        assert pts == 10
        assert report.duplicate_count == 1

    def test_three_duplicates_adds_20_pts(self, db_session):
        vendor = _seed_vendor(db_session)
        po = _seed_po(db_session, vendor.id)
        db_session.commit()

        for i in range(3):
            inv = _add_invoice(db_session, po.po_number, f"INV-DUP3-{i}",
                               status=InvoiceStatus.REVIEW_REQUIRED)
            _add_exception(db_session, inv.id, EXACT_DUPLICATE_TYPE, "dup")
        db_session.commit()

        report = _svc(db_session).calculate(vendor.id)
        pts = sum(b["points"] for b in report.score_breakdown if b["signal"] == "duplicate_history")
        assert pts == 20
        assert report.duplicate_count == 3

    def test_possible_duplicate_also_counted(self, db_session):
        vendor = _seed_vendor(db_session)
        po = _seed_po(db_session, vendor.id)
        db_session.commit()

        inv = _add_invoice(db_session, po.po_number, "INV-POSS-1",
                           status=InvoiceStatus.REVIEW_REQUIRED)
        _add_exception(db_session, inv.id, POSSIBLE_DUPLICATE_TYPE, "possible dup")
        db_session.commit()

        report = _svc(db_session).calculate(vendor.id)
        assert report.duplicate_count == 1
        pts = sum(b["points"] for b in report.score_breakdown if b["signal"] == "duplicate_history")
        assert pts == 10


# ---------------------------------------------------------------------------
# Test: PO mismatch signal (Signal 3)
# ---------------------------------------------------------------------------

class TestPOMismatchSignal:

    def test_one_po_mismatch_adds_10_pts(self, db_session):
        vendor = _seed_vendor(db_session)
        po = _seed_po(db_session, vendor.id)
        db_session.commit()

        inv = _add_invoice(db_session, po.po_number, "INV-POM-1",
                           status=InvoiceStatus.REVIEW_REQUIRED)
        _add_exception(db_session, inv.id, "PO_MISMATCH", "price diff")
        db_session.commit()

        report = _svc(db_session).calculate(vendor.id)
        pts = sum(b["points"] for b in report.score_breakdown if b["signal"] == "po_mismatch")
        assert pts == 10
        assert report.po_mismatch_count == 1

    def test_three_po_mismatches_adds_20_pts(self, db_session):
        vendor = _seed_vendor(db_session)
        po = _seed_po(db_session, vendor.id)
        db_session.commit()

        for i in range(3):
            inv = _add_invoice(db_session, po.po_number, f"INV-POM3-{i}",
                               status=InvoiceStatus.REVIEW_REQUIRED)
            _add_exception(db_session, inv.id, "PO_MISMATCH", "price diff")
        db_session.commit()

        report = _svc(db_session).calculate(vendor.id)
        pts = sum(b["points"] for b in report.score_breakdown if b["signal"] == "po_mismatch")
        assert pts == 20

    def test_po_mismatch_factor_text(self, db_session):
        vendor = _seed_vendor(db_session)
        po = _seed_po(db_session, vendor.id)
        db_session.commit()

        inv = _add_invoice(db_session, po.po_number, "INV-POMF-1",
                           status=InvoiceStatus.REVIEW_REQUIRED)
        _add_exception(db_session, inv.id, "PO_MISMATCH", "price diff")
        db_session.commit()

        report = _svc(db_session).calculate(vendor.id)
        assert any("PO mismatch" in f for f in report.risk_factors), (
            f"Expected PO mismatch factor. Got: {report.risk_factors}"
        )


# ---------------------------------------------------------------------------
# Test: contract violation signal (Signal 4)
# ---------------------------------------------------------------------------

class TestContractViolationSignal:

    def test_one_contract_violation_adds_15_pts(self, db_session):
        vendor = _seed_vendor(db_session)
        po = _seed_po(db_session, vendor.id)
        db_session.commit()

        inv = _add_invoice(db_session, po.po_number, "INV-CV-1",
                           status=InvoiceStatus.REVIEW_REQUIRED, total_amount=99999.0)
        _add_exception(db_session, inv.id, "CONTRACT_VIOLATION", "exceeds limit")
        db_session.commit()

        report = _svc(db_session).calculate(vendor.id)
        pts = sum(b["points"] for b in report.score_breakdown if b["signal"] == "contract_violation")
        assert pts == 15
        assert report.contract_violation_count == 1

    def test_three_contract_violations_adds_25_pts(self, db_session):
        vendor = _seed_vendor(db_session)
        po = _seed_po(db_session, vendor.id)
        db_session.commit()

        for i in range(3):
            inv = _add_invoice(db_session, po.po_number, f"INV-CV3-{i}",
                               status=InvoiceStatus.REVIEW_REQUIRED, total_amount=99999.0)
            _add_exception(db_session, inv.id, "CONTRACT_VIOLATION", "exceeds limit")
        db_session.commit()

        report = _svc(db_session).calculate(vendor.id)
        pts = sum(b["points"] for b in report.score_breakdown if b["signal"] == "contract_violation")
        assert pts == 25

    def test_contract_violation_factor_text(self, db_session):
        vendor = _seed_vendor(db_session)
        po = _seed_po(db_session, vendor.id)
        db_session.commit()

        inv = _add_invoice(db_session, po.po_number, "INV-CVF-1",
                           status=InvoiceStatus.REVIEW_REQUIRED)
        _add_exception(db_session, inv.id, "CONTRACT_VIOLATION", "exceeded")
        db_session.commit()

        report = _svc(db_session).calculate(vendor.id)
        assert any("contract violation" in f.lower() for f in report.risk_factors), (
            f"Expected contract violation factor. Got: {report.risk_factors}"
        )


# ---------------------------------------------------------------------------
# Test: score is capped at 100
# ---------------------------------------------------------------------------

class TestScoreCap:

    def test_score_capped_at_100(self, db_session):
        """A vendor with every possible exception type should not exceed 100."""
        vendor = _seed_vendor(db_session)
        po = _seed_po(db_session, vendor.id)
        db_session.commit()

        # Create 10 invoices each with every exception type
        for i in range(10):
            inv = _add_invoice(db_session, po.po_number, f"INV-MAX-{i:03d}",
                               status=InvoiceStatus.REVIEW_REQUIRED, days_ago=1)
            for exc_type in [
                EXACT_DUPLICATE_TYPE, "PO_MISMATCH",
                "CONTRACT_VIOLATION", "EXTRACTION_FAILED",
            ]:
                _add_exception(db_session, inv.id, exc_type, "test")
        db_session.commit()

        report = _svc(db_session).calculate(vendor.id)
        assert report.risk_score <= 100, (
            f"Score {report.risk_score} exceeds maximum of 100"
        )
        assert report.risk_level == "CRITICAL"


# ---------------------------------------------------------------------------
# Test: risk level boundaries
# ---------------------------------------------------------------------------

class TestRiskLevelBoundaries:

    def _make_report_with_score(self, db_session, target_pts: int) -> VendorRiskReport:
        """
        Produce a report whose raw score equals target_pts by engineering
        exactly the right exception mix.  We use contract violations (15 pts
        each) + PO mismatches (10 pts each) as building blocks.
        """
        vendor = _seed_vendor(db_session)
        po = _seed_po(db_session, vendor.id)
        db_session.commit()

        # We need a controllable score.  Easiest: just test _apply_signals directly.
        score, breakdown, factors = VendorRiskService._apply_signals(
            exception_rate=0.0,
            duplicate_count=0,
            po_mismatch_count=0,
            contract_violation_count=0,
            extraction_failure_count=0,
            recent_invoices=0,
            recent_exception_rate=0.0,
            total_invoices=0,
        )
        # Override: construct via known values instead
        return score, breakdown, factors

    def test_score_0_is_low(self):
        from services.vendor_risk_service import _risk_level
        assert _risk_level(0) == "LOW"

    def test_score_29_is_low(self):
        from services.vendor_risk_service import _risk_level
        assert _risk_level(29) == "LOW"

    def test_score_30_is_medium(self):
        from services.vendor_risk_service import _risk_level
        assert _risk_level(30) == "MEDIUM"

    def test_score_59_is_medium(self):
        from services.vendor_risk_service import _risk_level
        assert _risk_level(59) == "MEDIUM"

    def test_score_60_is_high(self):
        from services.vendor_risk_service import _risk_level
        assert _risk_level(60) == "HIGH"

    def test_score_79_is_high(self):
        from services.vendor_risk_service import _risk_level
        assert _risk_level(79) == "HIGH"

    def test_score_80_is_critical(self):
        from services.vendor_risk_service import _risk_level
        assert _risk_level(80) == "CRITICAL"

    def test_score_100_is_critical(self):
        from services.vendor_risk_service import _risk_level
        assert _risk_level(100) == "CRITICAL"


# ---------------------------------------------------------------------------
# Test: risk factors explain the score
# ---------------------------------------------------------------------------

class TestRiskFactors:

    def test_risk_factors_non_empty_when_exceptions_present(self, db_session):
        vendor = _seed_vendor(db_session)
        po = _seed_po(db_session, vendor.id)
        db_session.commit()

        inv = _add_invoice(db_session, po.po_number, "INV-RF-1",
                           status=InvoiceStatus.REVIEW_REQUIRED)
        _add_exception(db_session, inv.id, "PO_MISMATCH", "price diff")
        db_session.commit()

        report = _svc(db_session).calculate(vendor.id)
        assert len(report.risk_factors) > 0, "Expected at least one risk factor"

    def test_risk_factors_empty_for_clean_vendor(self, db_session):
        vendor = _seed_vendor(db_session)
        po = _seed_po(db_session, vendor.id)
        db_session.commit()

        _add_invoice(db_session, po.po_number, "INV-CLEAN-RF")
        db_session.commit()

        report = _svc(db_session).calculate(vendor.id)
        assert report.risk_factors == []

    def test_each_active_signal_produces_a_factor(self, db_session):
        vendor = _seed_vendor(db_session)
        po = _seed_po(db_session, vendor.id)
        db_session.commit()

        for i in range(3):
            inv = _add_invoice(db_session, po.po_number, f"INV-FACTOR-{i}",
                               status=InvoiceStatus.REVIEW_REQUIRED, days_ago=1)
            _add_exception(db_session, inv.id, EXACT_DUPLICATE_TYPE, "dup")
            _add_exception(db_session, inv.id, "PO_MISMATCH", "price diff")
            _add_exception(db_session, inv.id, "CONTRACT_VIOLATION", "exceeded")
        db_session.commit()

        report = _svc(db_session).calculate(vendor.id)
        # All three signals should appear in factors
        factor_text = " ".join(report.risk_factors).lower()
        assert "duplicate" in factor_text
        assert "po mismatch" in factor_text
        assert "contract violation" in factor_text

    def test_to_dict_includes_all_required_fields(self, db_session):
        vendor = _seed_vendor(db_session)
        db_session.commit()

        report = _svc(db_session).calculate(vendor.id)
        d = report.to_dict()

        required_keys = {
            "vendor_id", "vendor_name", "risk_score", "risk_level",
            "total_invoices", "total_exceptions", "exception_rate",
            "duplicate_count", "po_mismatch_count", "contract_violation_count",
            "risk_factors", "score_breakdown",
        }
        missing = required_keys - set(d.keys())
        assert not missing, f"to_dict() is missing keys: {missing}"


# ---------------------------------------------------------------------------
# Test: multiple vendors are calculated independently
# ---------------------------------------------------------------------------

class TestMultipleVendors:

    def test_two_vendors_calculated_independently(self, db_session):
        """
        Vendor A has multiple exceptions; Vendor B is clean.
        Their scores must be independent — Vendor A's exceptions must not
        inflate Vendor B's score.
        """
        vendor_a = _seed_vendor(db_session, "Risky Corp", "RISKY-001")
        vendor_b = _seed_vendor(db_session, "Clean Corp", "CLEAN-001")

        po_a = _seed_po(db_session, vendor_a.id, "PO-RISKY")
        po_b = _seed_po(db_session, vendor_b.id, "PO-CLEAN")
        db_session.commit()

        # Vendor A: 5 invoices all with PO_MISMATCH exceptions
        for i in range(5):
            inv = _add_invoice(db_session, po_a.po_number, f"INV-A-{i:03d}",
                               status=InvoiceStatus.REVIEW_REQUIRED)
            _add_exception(db_session, inv.id, "PO_MISMATCH", "price diff")

        # Vendor B: 5 clean invoices
        for i in range(5):
            _add_invoice(db_session, po_b.po_number, f"INV-B-{i:03d}")

        db_session.commit()

        report_a = _svc(db_session).calculate(vendor_a.id)
        report_b = _svc(db_session).calculate(vendor_b.id)

        assert report_a.risk_score > 0, "Vendor A should have a non-zero risk score"
        assert report_b.risk_score == 0, (
            f"Vendor B should be risk-free, got score {report_b.risk_score}"
        )
        assert report_a.risk_score > report_b.risk_score

    def test_vendor_b_not_affected_by_vendor_a_exceptions(self, db_session):
        vendor_a = _seed_vendor(db_session, "Bad Vendor", "BAD-001")
        vendor_b = _seed_vendor(db_session, "Good Vendor", "GOOD-001")

        po_a = _seed_po(db_session, vendor_a.id, "PO-BAD")
        po_b = _seed_po(db_session, vendor_b.id, "PO-GOOD")
        db_session.commit()

        # Add many exceptions for vendor A
        for i in range(10):
            inv = _add_invoice(db_session, po_a.po_number, f"INV-BAD-{i}",
                               status=InvoiceStatus.REVIEW_REQUIRED)
            _add_exception(db_session, inv.id, "CONTRACT_VIOLATION", "exceeded")
            _add_exception(db_session, inv.id, EXACT_DUPLICATE_TYPE, "dup")

        # Vendor B: single clean invoice
        _add_invoice(db_session, po_b.po_number, "INV-GOOD-1")
        db_session.commit()

        report_b = _svc(db_session).calculate(vendor_b.id)
        assert report_b.risk_score == 0
        assert report_b.total_exceptions == 0
        assert report_b.po_mismatch_count == 0
        assert report_b.contract_violation_count == 0
