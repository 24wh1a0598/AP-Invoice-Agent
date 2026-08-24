"""
Tests for the Human Review Workflow — ApprovalService
======================================================
Covers the service layer directly using an in-memory SQLite database.
No LLM calls, no LangGraph graph, no Groq API.

Run with:
    cd backend
    pytest tests/test_approval_service.py -v
"""

import sys
import os
import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.models import Invoice, InvoiceStatus, AuditLog, InvoiceException
from repositories.invoice_repo import InvoiceRepository
from services.approval_service import ApprovalService, ApprovalError


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


def _make_invoice(
    session,
    invoice_number: str = "INV-TEST-001",
    status: InvoiceStatus = InvoiceStatus.REVIEW_REQUIRED,
    total_amount: float = 1000.0,
) -> Invoice:
    """Persist an invoice in the given status and return it."""
    inv = Invoice(
        invoice_number=invoice_number,
        total_amount=total_amount,
        tax_amount=0.0,
        currency="USD",
        status=status,
        created_at=datetime.datetime.utcnow(),
    )
    session.add(inv)
    session.commit()
    session.refresh(inv)
    return inv


def _svc(session) -> ApprovalService:
    return ApprovalService(InvoiceRepository(session))


def _audit_entries(session, invoice_id: int):
    """Return all audit log rows for an invoice, ordered by timestamp."""
    return (
        session.query(AuditLog)
        .filter(AuditLog.invoice_id == invoice_id)
        .order_by(AuditLog.timestamp.asc())
        .all()
    )


# ---------------------------------------------------------------------------
# approve() — happy path
# ---------------------------------------------------------------------------

class TestApproveHappyPath:

    def test_approve_review_required_changes_status(self, db_session):
        inv = _make_invoice(db_session, status=InvoiceStatus.REVIEW_REQUIRED)
        _svc(db_session).approve(inv.id, acted_by="alice@corp.com")

        db_session.refresh(inv)
        assert inv.status == InvoiceStatus.APPROVED

    def test_approve_sets_approved_by(self, db_session):
        inv = _make_invoice(db_session)
        _svc(db_session).approve(inv.id, acted_by="alice@corp.com")

        db_session.refresh(inv)
        assert inv.approved_by == "alice@corp.com"

    def test_approve_sets_action_at(self, db_session):
        inv = _make_invoice(db_session)
        before = datetime.datetime.utcnow()
        _svc(db_session).approve(inv.id, acted_by="alice@corp.com")
        after = datetime.datetime.utcnow()

        db_session.refresh(inv)
        assert inv.action_at is not None
        assert before <= inv.action_at <= after

    def test_approve_returns_correct_dict(self, db_session):
        inv = _make_invoice(db_session, invoice_number="INV-APPROVE-01")
        result = _svc(db_session).approve(inv.id, acted_by="alice@corp.com")

        assert result["invoice_id"] == inv.id
        assert result["invoice_number"] == "INV-APPROVE-01"
        assert result["status"] == "APPROVED"
        assert result["approved_by"] == "alice@corp.com"
        assert result["action_at"] is not None

    def test_approve_creates_audit_log(self, db_session):
        inv = _make_invoice(db_session)
        _svc(db_session).approve(inv.id, acted_by="alice@corp.com")

        logs = _audit_entries(db_session, inv.id)
        assert len(logs) == 1
        assert logs[0].action == "APPROVED"
        assert logs[0].agent_name == "human_review"

    def test_approve_audit_log_contains_acted_by(self, db_session):
        inv = _make_invoice(db_session)
        _svc(db_session).approve(inv.id, acted_by="alice@corp.com")

        log = _audit_entries(db_session, inv.id)[0]
        assert log.details.get("acted_by") == "alice@corp.com"

    def test_approve_audit_log_contains_prev_and_new_status(self, db_session):
        inv = _make_invoice(db_session, status=InvoiceStatus.REVIEW_REQUIRED)
        _svc(db_session).approve(inv.id, acted_by="alice@corp.com")

        log = _audit_entries(db_session, inv.id)[0]
        assert log.details.get("prev_status") == "REVIEW_REQUIRED"
        assert log.details.get("new_status") == "APPROVED"


# ---------------------------------------------------------------------------
# reject() — happy path
# ---------------------------------------------------------------------------

class TestRejectHappyPath:

    def test_reject_review_required_changes_status(self, db_session):
        inv = _make_invoice(db_session)
        _svc(db_session).reject(inv.id, acted_by="bob@corp.com",
                                rejection_reason="Price inflated.")

        db_session.refresh(inv)
        assert inv.status == InvoiceStatus.REJECTED

    def test_reject_stores_rejection_reason(self, db_session):
        inv = _make_invoice(db_session)
        _svc(db_session).reject(inv.id, acted_by="bob@corp.com",
                                rejection_reason="Duplicate submission.")

        db_session.refresh(inv)
        assert inv.rejection_reason == "Duplicate submission."

    def test_reject_sets_approved_by(self, db_session):
        inv = _make_invoice(db_session)
        _svc(db_session).reject(inv.id, acted_by="bob@corp.com",
                                rejection_reason="Invalid PO.")

        db_session.refresh(inv)
        assert inv.approved_by == "bob@corp.com"

    def test_reject_sets_action_at(self, db_session):
        inv = _make_invoice(db_session)
        before = datetime.datetime.utcnow()
        _svc(db_session).reject(inv.id, acted_by="bob@corp.com",
                                rejection_reason="Test reason.")
        after = datetime.datetime.utcnow()

        db_session.refresh(inv)
        assert before <= inv.action_at <= after

    def test_reject_returns_correct_dict(self, db_session):
        inv = _make_invoice(db_session, invoice_number="INV-REJECT-01")
        result = _svc(db_session).reject(inv.id, acted_by="bob@corp.com",
                                         rejection_reason="Price too high.")

        assert result["status"] == "REJECTED"
        assert result["rejection_reason"] == "Price too high."
        assert result["approved_by"] == "bob@corp.com"

    def test_reject_creates_audit_log(self, db_session):
        inv = _make_invoice(db_session)
        _svc(db_session).reject(inv.id, acted_by="bob@corp.com",
                                rejection_reason="PO mismatch confirmed.")

        logs = _audit_entries(db_session, inv.id)
        assert len(logs) == 1
        assert logs[0].action == "REJECTED"
        assert logs[0].agent_name == "human_review"

    def test_reject_audit_log_contains_reason(self, db_session):
        inv = _make_invoice(db_session)
        _svc(db_session).reject(inv.id, acted_by="bob@corp.com",
                                rejection_reason="Contract expired.")

        log = _audit_entries(db_session, inv.id)[0]
        assert log.details.get("reason") == "Contract expired."
        assert log.details.get("acted_by") == "bob@corp.com"
        assert log.details.get("prev_status") == "REVIEW_REQUIRED"
        assert log.details.get("new_status") == "REJECTED"


# ---------------------------------------------------------------------------
# reject() — validation
# ---------------------------------------------------------------------------

class TestRejectValidation:

    def test_reject_empty_reason_raises(self, db_session):
        inv = _make_invoice(db_session)
        with pytest.raises(ApprovalError, match="rejection_reason"):
            _svc(db_session).reject(inv.id, acted_by="bob@corp.com",
                                    rejection_reason="")

    def test_reject_whitespace_reason_raises(self, db_session):
        inv = _make_invoice(db_session)
        with pytest.raises(ApprovalError, match="rejection_reason"):
            _svc(db_session).reject(inv.id, acted_by="bob@corp.com",
                                    rejection_reason="   ")

    def test_reject_strips_whitespace_from_reason(self, db_session):
        inv = _make_invoice(db_session)
        _svc(db_session).reject(inv.id, acted_by="bob@corp.com",
                                rejection_reason="  Fraud suspected.  ")
        db_session.refresh(inv)
        assert inv.rejection_reason == "Fraud suspected."


# ---------------------------------------------------------------------------
# request_info() — happy path
# ---------------------------------------------------------------------------

class TestRequestInfoHappyPath:

    def test_request_info_preserves_review_required_status(self, db_session):
        inv = _make_invoice(db_session)
        _svc(db_session).request_info(inv.id, acted_by="carol@corp.com",
                                      message="Send delivery note.")

        db_session.refresh(inv)
        assert inv.status == InvoiceStatus.REVIEW_REQUIRED

    def test_request_info_sets_action_at(self, db_session):
        inv = _make_invoice(db_session)
        before = datetime.datetime.utcnow()
        _svc(db_session).request_info(inv.id, acted_by="carol@corp.com",
                                      message="Send delivery note.")
        after = datetime.datetime.utcnow()

        db_session.refresh(inv)
        assert before <= inv.action_at <= after

    def test_request_info_returns_correct_dict(self, db_session):
        inv = _make_invoice(db_session, invoice_number="INV-INFO-01")
        result = _svc(db_session).request_info(inv.id, acted_by="carol@corp.com",
                                               message="Need signed PO copy.")

        assert result["invoice_id"] == inv.id
        assert result["status"] == "REVIEW_REQUIRED"
        assert result["action"] == "INFO_REQUESTED"
        assert result["message"] == "Need signed PO copy."
        assert result["acted_by"] == "carol@corp.com"

    def test_request_info_creates_audit_log(self, db_session):
        inv = _make_invoice(db_session)
        _svc(db_session).request_info(inv.id, acted_by="carol@corp.com",
                                      message="Please resend invoice.")

        logs = _audit_entries(db_session, inv.id)
        assert len(logs) == 1
        assert logs[0].action == "INFO_REQUESTED"
        assert logs[0].details.get("acted_by") == "carol@corp.com"
        assert logs[0].details.get("reason") == "Please resend invoice."
        assert logs[0].details.get("prev_status") == "REVIEW_REQUIRED"
        assert logs[0].details.get("new_status") == "REVIEW_REQUIRED"

    def test_request_info_empty_message_raises(self, db_session):
        inv = _make_invoice(db_session)
        with pytest.raises(ApprovalError, match="message"):
            _svc(db_session).request_info(inv.id, acted_by="carol@corp.com",
                                          message="")


# ---------------------------------------------------------------------------
# State transition validation — approve
# ---------------------------------------------------------------------------

class TestApproveInvalidTransitions:

    def test_approve_nonexistent_invoice_raises_404(self, db_session):
        exc = pytest.raises(ApprovalError,
                            _svc(db_session).approve, 99999, acted_by="x@x.com")
        assert exc.value.http_code == 404

    def test_approve_already_approved_raises_422(self, db_session):
        inv = _make_invoice(db_session, status=InvoiceStatus.APPROVED)
        exc = pytest.raises(ApprovalError,
                            _svc(db_session).approve, inv.id, acted_by="x@x.com")
        assert exc.value.http_code == 422

    def test_approve_rejected_invoice_raises_422(self, db_session):
        inv = _make_invoice(db_session, status=InvoiceStatus.REJECTED)
        exc = pytest.raises(ApprovalError,
                            _svc(db_session).approve, inv.id, acted_by="x@x.com")
        assert exc.value.http_code == 422

    def test_approve_paid_invoice_raises_422(self, db_session):
        inv = _make_invoice(db_session, status=InvoiceStatus.PAID)
        exc = pytest.raises(ApprovalError,
                            _svc(db_session).approve, inv.id, acted_by="x@x.com")
        assert exc.value.http_code == 422

    def test_approve_straight_through_raises_422(self, db_session):
        """Auto-approved invoices must not accept a second manual approval."""
        inv = _make_invoice(db_session, status=InvoiceStatus.STRAIGHT_THROUGH)
        exc = pytest.raises(ApprovalError,
                            _svc(db_session).approve, inv.id, acted_by="x@x.com")
        assert exc.value.http_code == 422

    def test_approve_pending_invoice_raises_422(self, db_session):
        """PENDING means the pipeline hasn't finished; must not be approved."""
        inv = _make_invoice(db_session, status=InvoiceStatus.PENDING)
        exc = pytest.raises(ApprovalError,
                            _svc(db_session).approve, inv.id, acted_by="x@x.com")
        assert exc.value.http_code == 422


# ---------------------------------------------------------------------------
# State transition validation — reject
# ---------------------------------------------------------------------------

class TestRejectInvalidTransitions:

    def test_reject_nonexistent_invoice_raises_404(self, db_session):
        exc = pytest.raises(ApprovalError,
                            _svc(db_session).reject, 99999,
                            acted_by="x@x.com", rejection_reason="reason")
        assert exc.value.http_code == 404

    def test_reject_already_rejected_raises_422(self, db_session):
        inv = _make_invoice(db_session, status=InvoiceStatus.REJECTED)
        exc = pytest.raises(ApprovalError,
                            _svc(db_session).reject, inv.id,
                            acted_by="x@x.com", rejection_reason="again")
        assert exc.value.http_code == 422

    def test_reject_approved_invoice_raises_422(self, db_session):
        inv = _make_invoice(db_session, status=InvoiceStatus.APPROVED)
        exc = pytest.raises(ApprovalError,
                            _svc(db_session).reject, inv.id,
                            acted_by="x@x.com", rejection_reason="mistake")
        assert exc.value.http_code == 422

    def test_reject_paid_invoice_raises_422(self, db_session):
        inv = _make_invoice(db_session, status=InvoiceStatus.PAID)
        exc = pytest.raises(ApprovalError,
                            _svc(db_session).reject, inv.id,
                            acted_by="x@x.com", rejection_reason="late")
        assert exc.value.http_code == 422

    def test_reject_straight_through_raises_422(self, db_session):
        inv = _make_invoice(db_session, status=InvoiceStatus.STRAIGHT_THROUGH)
        exc = pytest.raises(ApprovalError,
                            _svc(db_session).reject, inv.id,
                            acted_by="x@x.com", rejection_reason="no reason")
        assert exc.value.http_code == 422


# ---------------------------------------------------------------------------
# State transition validation — request_info
# ---------------------------------------------------------------------------

class TestRequestInfoInvalidTransitions:

    def test_request_info_nonexistent_raises_404(self, db_session):
        exc = pytest.raises(ApprovalError,
                            _svc(db_session).request_info, 99999,
                            acted_by="x@x.com", message="?")
        assert exc.value.http_code == 404

    def test_request_info_approved_invoice_raises_422(self, db_session):
        inv = _make_invoice(db_session, status=InvoiceStatus.APPROVED)
        exc = pytest.raises(ApprovalError,
                            _svc(db_session).request_info, inv.id,
                            acted_by="x@x.com", message="still need info")
        assert exc.value.http_code == 422

    def test_request_info_rejected_invoice_raises_422(self, db_session):
        inv = _make_invoice(db_session, status=InvoiceStatus.REJECTED)
        exc = pytest.raises(ApprovalError,
                            _svc(db_session).request_info, inv.id,
                            acted_by="x@x.com", message="still need info")
        assert exc.value.http_code == 422

    def test_request_info_paid_invoice_raises_422(self, db_session):
        inv = _make_invoice(db_session, status=InvoiceStatus.PAID)
        exc = pytest.raises(ApprovalError,
                            _svc(db_session).request_info, inv.id,
                            acted_by="x@x.com", message="info?")
        assert exc.value.http_code == 422


# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------

class TestReviewQueue:

    def test_queue_returns_only_review_required(self, db_session):
        """Only REVIEW_REQUIRED invoices appear in the queue."""
        _make_invoice(db_session, "INV-Q-RR-1", InvoiceStatus.REVIEW_REQUIRED)
        _make_invoice(db_session, "INV-Q-ST-1", InvoiceStatus.STRAIGHT_THROUGH)
        _make_invoice(db_session, "INV-Q-AP-1", InvoiceStatus.APPROVED)
        _make_invoice(db_session, "INV-Q-RJ-1", InvoiceStatus.REJECTED)

        repo = InvoiceRepository(db_session)
        queue = repo.get_review_queue()

        assert len(queue) == 1
        assert queue[0].invoice_number == "INV-Q-RR-1"

    def test_queue_excludes_pending_placeholders(self, db_session):
        """PENDING-prefixed invoice numbers must not appear in the queue."""
        # Simulate a PENDING placeholder that somehow got stuck in REVIEW_REQUIRED
        inv = Invoice(
            invoice_number="PENDING-abc12345-file.pdf",
            total_amount=0.0, tax_amount=0.0, currency="USD",
            status=InvoiceStatus.REVIEW_REQUIRED,
            created_at=datetime.datetime.utcnow(),
        )
        db_session.add(inv)
        db_session.commit()

        repo = InvoiceRepository(db_session)
        queue = repo.get_review_queue()
        assert len(queue) == 0

    def test_queue_ordered_oldest_first(self, db_session):
        """Oldest REVIEW_REQUIRED invoices come first."""
        now = datetime.datetime.utcnow()
        for days_ago, num in [(3, "INV-OLD"), (1, "INV-NEW"), (2, "INV-MID")]:
            inv = Invoice(
                invoice_number=num,
                total_amount=100.0, tax_amount=0.0, currency="USD",
                status=InvoiceStatus.REVIEW_REQUIRED,
                created_at=now - datetime.timedelta(days=days_ago),
            )
            db_session.add(inv)
        db_session.commit()

        repo = InvoiceRepository(db_session)
        queue = repo.get_review_queue()

        assert [i.invoice_number for i in queue] == ["INV-OLD", "INV-MID", "INV-NEW"]

    def test_queue_respects_skip_and_limit(self, db_session):
        for i in range(5):
            _make_invoice(db_session, f"INV-PAGE-{i:02d}")

        repo = InvoiceRepository(db_session)
        page1 = repo.get_review_queue(skip=0, limit=2)
        page2 = repo.get_review_queue(skip=2, limit=2)

        assert len(page1) == 2
        assert len(page2) == 2
        # no overlap
        ids1 = {i.invoice_number for i in page1}
        ids2 = {i.invoice_number for i in page2}
        assert ids1.isdisjoint(ids2)

    def test_empty_queue_returns_empty_list(self, db_session):
        repo = InvoiceRepository(db_session)
        assert repo.get_review_queue() == []
