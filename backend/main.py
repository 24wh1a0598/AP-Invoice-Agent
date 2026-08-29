import os
import uuid
from dotenv import load_dotenv

# Load environment variables before any other imports
load_dotenv()

from fastapi import FastAPI, UploadFile, Depends, File, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import func
import uvicorn
import logging

from database import get_db, engine, Base
from tools.ocr_engine import OCREngine, OCRError
from agents.graph import app_agent
from models.models import Invoice, InvoiceStatus, AuditLog, InvoiceException
from repositories.invoice_repo import InvoiceRepository

logger = logging.getLogger("ap_agent.api")
logging.basicConfig(level=logging.INFO)

# Create all tables on startup
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="AP Invoice Exception Agent",
    description="Automated Accounts Payable invoice processing with AI-driven exception detection.",
    version="1.0.0",
)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/", tags=["Health"])
def health_check():
    return {
        "status": "online",
        "agent": "AP Invoice Agent",
        "version": "1.0.0",
        "database": "connected",
    }


# ---------------------------------------------------------------------------
# POST /seed-db — seeds reference data (vendor, PO, contract)
# Call this once after first deploy to populate the database.
# ---------------------------------------------------------------------------

@app.post("/seed-db", tags=["Health"])
def seed_db(db: Session = Depends(get_db)):
    """
    Seeds the database with the Dell vendor, PO-DELL-2024, and CTR-DELL-2024
    reference data required to process the sample invoices.
    Safe to call multiple times — skips records that already exist.
    """
    from models.models import Vendor, PurchaseOrder, Contract
    results = []

    try:
        # --- Vendor ---
        vendor = db.query(Vendor).filter(Vendor.vendor_code == "DELL-001").first()
        if vendor:
            results.append("Vendor DELL-001 already exists — skipped.")
        else:
            vendor = Vendor(name="Dell Technologies Inc.", vendor_code="DELL-001")
            db.add(vendor)
            db.flush()
            results.append(f"Vendor created: Dell Technologies Inc. (id={vendor.id})")

        # --- Purchase Order ---
        if db.query(PurchaseOrder).filter(PurchaseOrder.po_number == "PO-DELL-2024").first():
            results.append("PO PO-DELL-2024 already exists — skipped.")
        else:
            po = PurchaseOrder(
                po_number="PO-DELL-2024",
                vendor_id=vendor.id,
                total_amount=10775.00,
                status="OPEN",
                line_items=[
                    {"description": "Dell Latitude 5540 Laptop",      "quantity": 10.0, "unit_price": 850.00,  "total": 8500.00},
                    {"description": 'Dell 27" Monitor P2723D',         "quantity": 5.0,  "unit_price": 320.00,  "total": 1600.00},
                    {"description": "Dell Wireless Keyboard & Mouse",  "quantity": 15.0, "unit_price": 45.00,   "total": 675.00},
                ],
            )
            db.add(po)
            results.append("PO created: PO-DELL-2024 (total $10,775.00)")

        # --- Contract ---
        if db.query(Contract).filter(Contract.contract_number == "CTR-DELL-2024").first():
            results.append("Contract CTR-DELL-2024 already exists — skipped.")
        else:
            contract = Contract(
                contract_number="CTR-DELL-2024",
                vendor_id=vendor.id,
                max_amount=50000.00,
            )
            db.add(contract)
            results.append("Contract created: CTR-DELL-2024 (max $50,000.00)")

        db.commit()
        return {"status": "ok", "results": results}

    except SQLAlchemyError as exc:
        db.rollback()
        logger.error(f"seed_db error: {exc}")
        raise HTTPException(status_code=500, detail=f"Seed failed: {exc}")

@app.get("/diag", tags=["Health"])
def diag():
    import requests as http_requests
    import time

    groq_key = os.getenv("GROQ_API_KEY")
    key_info = {
        "present": bool(groq_key),
        "length": len(groq_key) if groq_key else 0,
        "prefix": (groq_key[:7] + "***") if groq_key and len(groq_key) > 7 else "TOO_SHORT",
    }

    probe = {}
    try:
        t0 = time.time()
        r = http_requests.get("https://api.groq.com", timeout=15)
        probe = {
            "reachable": True,
            "status_code": r.status_code,
            "elapsed_s": round(time.time() - t0, 2),
            "body_preview": r.text[:300],
        }
    except Exception as exc:
        import traceback
        probe = {
            "reachable": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }

    return {"groq_api_key": key_info, "groq_connectivity": probe}


# ---------------------------------------------------------------------------
# POST /upload-invoice — main processing endpoint
# ---------------------------------------------------------------------------

@app.post("/upload-invoice", tags=["Processing"])
async def process_invoice(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Accepts a PDF or image invoice, runs the full agent pipeline, and returns
    the structured result including extracted fields, exceptions, decision,
    and reasoning chain.
    """
    # --- Validate file type ---
    allowed_types = {"application/pdf", "image/png", "image/jpeg", "image/jpg"}
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{file.content_type}'. "
                   f"Accepted: PDF, PNG, JPG.",
        )

    # --- Read file bytes ---
    try:
        content = await file.read()
    except Exception as exc:
        logger.error(f"Failed to read uploaded file: {exc}")
        raise HTTPException(status_code=400, detail="Could not read uploaded file.")

    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    # --- OCR ---
    try:
        raw_text = OCREngine.extract_text(content, file.content_type)
    except OCRError as exc:
        logger.error(f"OCR failed: {exc}")
        raise HTTPException(status_code=422, detail=f"OCR extraction failed: {exc}")
    except Exception as exc:
        logger.error(f"Unexpected OCR error: {exc}")
        raise HTTPException(status_code=500, detail="Unexpected error during OCR processing.")

    # --- Persist initial PENDING invoice record ---
    # Use a UUID-based placeholder so re-uploading the same file never
    # hits the unique constraint on invoice_number.
    try:
        repo = InvoiceRepository(db)
        invoice = Invoice(
            invoice_number=f"PENDING-{uuid.uuid4().hex[:8]}-{file.filename}",
            status=InvoiceStatus.PENDING,
            total_amount=0.0,
            tax_amount=0.0,
            currency="USD",
        )
        invoice = repo.save_invoice(invoice)
        invoice_id = invoice.id
    except SQLAlchemyError as exc:
        logger.error(f"Database error saving invoice: {exc}")
        raise HTTPException(status_code=500, detail="Database error while creating invoice record.")

    # --- Run agent pipeline ---
    initial_state = {
        "raw_text": raw_text,
        "extracted_data": {},
        "exceptions": [],
        "status": "PENDING",
        "reasoning": ["File received. Starting extraction..."],
        "invoice_id": invoice_id,
    }

    try:
        final_state = await app_agent.ainvoke(initial_state)
    except Exception as exc:
        logger.error(f"Agent pipeline error for invoice {invoice_id}: {exc}")
        # Mark invoice as rejected so it isn't left as PENDING
        try:
            repo.update_invoice_status(invoice_id, InvoiceStatus.REJECTED)
        except Exception:
            pass
        raise HTTPException(
            status_code=500,
            detail=f"Agent pipeline encountered an unexpected error: {exc}",
        )

    # --- Update invoice record with extracted values ---
    extracted = final_state.get("extracted_data", {})
    agent_status = final_state.get("status", "EXCEPTION")
    exceptions_list = list(final_state.get("exceptions", []))

    status_map = {
        "STRAIGHT_THROUGH": InvoiceStatus.STRAIGHT_THROUGH,
        "EXCEPTION": InvoiceStatus.REVIEW_REQUIRED,
        "EXTRACTION_FAILED": InvoiceStatus.REJECTED,
    }
    db_status = status_map.get(agent_status, InvoiceStatus.REVIEW_REQUIRED)
    extracted_invoice_number = extracted.get("invoice_number")

    try:
        # Refresh the session so it can query after the async agent ran
        db.expire_all()

        # --- Persist final invoice state ---
        invoice.invoice_number = extracted_invoice_number or invoice.invoice_number
        invoice.total_amount = extracted.get("total_amount") or 0.0
        invoice.tax_amount = extracted.get("tax_amount") or 0.0
        invoice.currency = extracted.get("currency") or "USD"
        invoice.status = db_status
        db.commit()

        if exceptions_list:
            repo.save_exceptions(invoice_id, exceptions_list)

    except SQLAlchemyError as exc:
        logger.error(f"Database error updating invoice {invoice_id}: {exc}")
        db.rollback()

    # Pull the extraction error out of reasoning for easy visibility
    extraction_error = None
    for step in final_state.get("reasoning", []):
        if step.startswith("Extraction failed:"):
            extraction_error = step
            break

    return {
        "invoice_id": invoice_id,
        "invoice_number": invoice.invoice_number,
        "status": agent_status,
        "extracted_fields": extracted,
        "exceptions": exceptions_list,
        "reasoning": final_state.get("reasoning", []),
        "extraction_error": extraction_error,
    }


# ---------------------------------------------------------------------------
# GET /invoices — list all invoices
# ---------------------------------------------------------------------------

@app.get("/invoices", tags=["Invoices"])
def list_invoices(
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """
    Returns a paginated list of all invoices with their current status.
    """
    try:
        invoices = (
            db.query(Invoice)
            .order_by(Invoice.created_at.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )
    except SQLAlchemyError as exc:
        logger.error(f"Database error listing invoices: {exc}")
        raise HTTPException(status_code=500, detail="Database error while fetching invoices.")

    return [
        {
            "id": inv.id,
            "invoice_number": inv.invoice_number,
            "status": inv.status.value if inv.status else "UNKNOWN",
            "total_amount": inv.total_amount,
            "currency": inv.currency,
            "created_at": inv.created_at.isoformat() if inv.created_at else None,
        }
        for inv in invoices
    ]


# ---------------------------------------------------------------------------
# GET /invoice/{id} — single invoice detail
# ---------------------------------------------------------------------------

@app.get("/invoice/{invoice_id}", tags=["Invoices"])
def get_invoice(invoice_id: int, db: Session = Depends(get_db)):
    """
    Returns the full detail of a single invoice including its exceptions.
    """
    try:
        repo = InvoiceRepository(db)
        invoice = repo.get_invoice(invoice_id)
    except SQLAlchemyError as exc:
        logger.error(f"Database error fetching invoice {invoice_id}: {exc}")
        raise HTTPException(status_code=500, detail="Database error.")

    if not invoice:
        raise HTTPException(status_code=404, detail=f"Invoice {invoice_id} not found.")

    exceptions = (
        db.query(InvoiceException)
        .filter(InvoiceException.invoice_id == invoice_id)
        .all()
    )

    return {
        "id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "status": invoice.status.value if invoice.status else "UNKNOWN",
        "total_amount": invoice.total_amount,
        "tax_amount": invoice.tax_amount,
        "currency": invoice.currency,
        "created_at": invoice.created_at.isoformat() if invoice.created_at else None,
        "exceptions": [
            {
                "type": exc.exception_type,
                "description": exc.description,
            }
            for exc in exceptions
        ],
    }


# ---------------------------------------------------------------------------
# GET /invoice/{id}/audit — audit trail for one invoice
# ---------------------------------------------------------------------------

@app.get("/invoice/{invoice_id}/audit", tags=["Audit"])
def get_audit_trail(invoice_id: int, db: Session = Depends(get_db)):
    """
    Returns the complete, ordered audit trail for a single invoice.
    Each record shows which agent node ran, what decision it made,
    and the full reasoning payload.
    """
    try:
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=500, detail="Database error.")

    if not invoice:
        raise HTTPException(status_code=404, detail=f"Invoice {invoice_id} not found.")

    try:
        logs = (
            db.query(AuditLog)
            .filter(AuditLog.invoice_id == invoice_id)
            .order_by(AuditLog.timestamp.asc())
            .all()
        )
    except SQLAlchemyError as exc:
        logger.error(f"Database error fetching audit logs for invoice {invoice_id}: {exc}")
        raise HTTPException(status_code=500, detail="Database error fetching audit logs.")

    return {
        "invoice_id": invoice_id,
        "invoice_number": invoice.invoice_number,
        "audit_trail": [
            {
                "id": log.id,
                "agent": log.agent_name,
                "action": log.action,
                "details": log.details,
                "timestamp": log.timestamp.isoformat() if log.timestamp else None,
            }
            for log in logs
        ],
    }


# ---------------------------------------------------------------------------
# GET /stats — dashboard summary statistics
# ---------------------------------------------------------------------------

@app.get("/stats", tags=["Dashboard"])
def get_stats(db: Session = Depends(get_db)):
    """
    Returns aggregated counts for the dashboard.
    """
    try:
        total = db.query(Invoice).count()
        straight_through = db.query(Invoice).filter(
            Invoice.status == InvoiceStatus.STRAIGHT_THROUGH
        ).count()
        review_required = db.query(Invoice).filter(
            Invoice.status == InvoiceStatus.REVIEW_REQUIRED
        ).count()
        rejected = db.query(Invoice).filter(
            Invoice.status == InvoiceStatus.REJECTED
        ).count()
        total_value = db.query(
            func.sum(Invoice.total_amount)
        ).scalar() or 0.0
    except SQLAlchemyError as exc:
        logger.error(f"Database error fetching stats: {exc}")
        raise HTTPException(status_code=500, detail="Database error fetching stats.")

    stp_pct = round((straight_through / total * 100), 1) if total > 0 else 0.0

    return {
        "total_invoices": total,
        "straight_through": straight_through,
        "straight_through_pct": stp_pct,
        "review_required": review_required,
        "rejected": rejected,
        "total_scheduled_value": round(total_value, 2),
    }


# ---------------------------------------------------------------------------
# GET /invoices/review-queue — human review queue
# ---------------------------------------------------------------------------

@app.get("/invoices/review-queue", tags=["Human Review"])
def get_review_queue(
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """
    Returns invoices currently requiring human review, ordered oldest-first
    so AP clerks work through the queue in submission order.

    Each item includes enough context for the reviewer: invoice metadata,
    all raised exceptions, and vendor / PO references.
    """
    from repositories.invoice_repo import InvoiceRepository

    repo = InvoiceRepository(db)
    try:
        invoices = repo.get_review_queue(skip=skip, limit=limit)
    except SQLAlchemyError as exc:
        logger.error(f"Database error fetching review queue: {exc}")
        raise HTTPException(status_code=500, detail="Database error fetching review queue.")

    result = []
    for inv in invoices:
        exceptions = (
            db.query(InvoiceException)
            .filter(InvoiceException.invoice_id == inv.id)
            .all()
        )
        result.append({
            "id": inv.id,
            "invoice_number": inv.invoice_number,
            "vendor": inv.vendor.name if inv.vendor else None,
            "po_number": inv.po_number,
            "total_amount": inv.total_amount,
            "currency": inv.currency,
            "status": inv.status.value if inv.status else "UNKNOWN",
            "created_at": inv.created_at.isoformat() if inv.created_at else None,
            "exceptions": [
                {"type": e.exception_type, "description": e.description}
                for e in exceptions
            ],
        })
    return result


# ---------------------------------------------------------------------------
# PATCH /invoice/{id}/approve — approve a flagged invoice
# ---------------------------------------------------------------------------

@app.patch("/invoice/{invoice_id}/approve", tags=["Human Review"])
def approve_invoice(
    invoice_id: int,
    body: dict,
    db: Session = Depends(get_db),
):
    """
    Approve a REVIEW_REQUIRED invoice.

    Request body:
        { "acted_by": "alice@corp.com" }

    - acted_by: Identity of the approver (required).
      NOTE: No authentication system exists yet.  The caller supplies
      their identity as a plain string.  This will be replaced with a
      JWT claim when authentication is added.

    State transition: REVIEW_REQUIRED → APPROVED
    All other transitions are rejected with HTTP 422.
    """
    from services.approval_service import ApprovalService, ApprovalError

    acted_by = (body.get("acted_by") or "").strip()
    if not acted_by:
        raise HTTPException(status_code=422, detail="acted_by is required.")

    repo = InvoiceRepository(db)
    try:
        result = ApprovalService(repo).approve(invoice_id, acted_by=acted_by)
    except ApprovalError as exc:
        raise HTTPException(status_code=exc.http_code, detail=str(exc))
    except SQLAlchemyError as exc:
        logger.error(f"Database error approving invoice {invoice_id}: {exc}")
        raise HTTPException(status_code=500, detail="Database error during approval.")

    return result


# ---------------------------------------------------------------------------
# PATCH /invoice/{id}/reject — reject a flagged invoice
# ---------------------------------------------------------------------------

@app.patch("/invoice/{invoice_id}/reject", tags=["Human Review"])
def reject_invoice(
    invoice_id: int,
    body: dict,
    db: Session = Depends(get_db),
):
    """
    Reject a REVIEW_REQUIRED invoice.

    Request body:
        {
            "acted_by": "bob@corp.com",
            "rejection_reason": "Price inflated compared to contract terms."
        }

    - acted_by: Identity of the rejector (required).
    - rejection_reason: Non-empty explanation (required).

    State transition: REVIEW_REQUIRED → REJECTED
    All other transitions are rejected with HTTP 422.
    """
    from services.approval_service import ApprovalService, ApprovalError

    acted_by = (body.get("acted_by") or "").strip()
    rejection_reason = (body.get("rejection_reason") or "").strip()

    if not acted_by:
        raise HTTPException(status_code=422, detail="acted_by is required.")

    repo = InvoiceRepository(db)
    try:
        result = ApprovalService(repo).reject(
            invoice_id,
            acted_by=acted_by,
            rejection_reason=rejection_reason,
        )
    except ApprovalError as exc:
        raise HTTPException(status_code=exc.http_code, detail=str(exc))
    except SQLAlchemyError as exc:
        logger.error(f"Database error rejecting invoice {invoice_id}: {exc}")
        raise HTTPException(status_code=500, detail="Database error during rejection.")

    return result


# ---------------------------------------------------------------------------
# POST /invoice/{id}/request-info — request additional information
# ---------------------------------------------------------------------------

@app.post("/invoice/{invoice_id}/request-info", tags=["Human Review"])
def request_invoice_info(
    invoice_id: int,
    body: dict,
    db: Session = Depends(get_db),
):
    """
    Request additional information for a REVIEW_REQUIRED invoice.

    Request body:
        {
            "acted_by": "carol@corp.com",
            "message": "Please provide the original signed delivery note."
        }

    - acted_by: Identity of the requestor (required).
    - message: Non-empty description of what is needed (required).

    The invoice status remains REVIEW_REQUIRED.  An audit log entry is created.
    """
    from services.approval_service import ApprovalService, ApprovalError

    acted_by = (body.get("acted_by") or "").strip()
    message = (body.get("message") or "").strip()

    if not acted_by:
        raise HTTPException(status_code=422, detail="acted_by is required.")

    repo = InvoiceRepository(db)
    try:
        result = ApprovalService(repo).request_info(
            invoice_id,
            acted_by=acted_by,
            message=message,
        )
    except ApprovalError as exc:
        raise HTTPException(status_code=exc.http_code, detail=str(exc))
    except SQLAlchemyError as exc:
        logger.error(f"Database error on request-info for invoice {invoice_id}: {exc}")
        raise HTTPException(status_code=500, detail="Database error during request-info.")

    return result


# ---------------------------------------------------------------------------
# GET /vendor/{vendor_id}/risk — vendor risk intelligence
# ---------------------------------------------------------------------------

@app.get("/vendor/{vendor_id}/risk", tags=["Vendor Risk"])
def get_vendor_risk(vendor_id: int, db: Session = Depends(get_db)):
    """
    Returns a deterministic risk score and explanation for the given vendor.

    The score (0–100) is calculated from historical invoice and exception data
    already stored in the database — no LLM call is made.

    Risk levels: LOW (0–29) | MEDIUM (30–59) | HIGH (60–79) | CRITICAL (80–100)
    """
    from services.vendor_risk_service import VendorRiskService

    repo = InvoiceRepository(db)
    try:
        report = VendorRiskService(repo).calculate(vendor_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except SQLAlchemyError as exc:
        logger.error(f"Database error calculating vendor risk for vendor {vendor_id}: {exc}")
        raise HTTPException(status_code=500, detail="Database error calculating vendor risk.")

    return report.to_dict()


# ---------------------------------------------------------------------------
# POST /batch/process — synthetic batch evaluation
# ---------------------------------------------------------------------------

@app.post("/batch/process", tags=["Batch"])
def batch_process(body: dict):
    """
    Generate a synthetic invoice batch, process it through the existing
    pipeline, and return a structured evaluation report.

    No real LLM calls are made — pre-seeded extracted_data bypasses the
    extraction node.

    Request body (all fields optional):
    {
        "count": 20,
        "seed": 42,
        "distribution": {
            "CLEAN": 14,
            "PO_PRICE_MISMATCH": 2,
            "QUANTITY_MISMATCH": 1,
            "UNKNOWN_PO": 1,
            "CONTRACT_VIOLATION": 1,
            "DUPLICATE": 1,
            "EXTRACTION_FAILURE": 0
        }
    }

    - count: total number of invoices (default 20). Ignored when distribution
      is provided (the distribution counts are used directly).
    - seed: integer random seed for reproducible shuffling (default None).
    - distribution: explicit scenario distribution dict. If omitted, a
      proportional distribution is built from DEFAULT_DISTRIBUTION scaled
      to count.

    Returns the full EvaluationReport as JSON.
    """
    from batch.invoice_generator import (
        SyntheticInvoiceGenerator,
        DEFAULT_DISTRIBUTION,
        ALL_SCENARIOS,
    )
    from batch.batch_processor import BatchProcessor
    from batch.evaluation_service import EvaluationService

    count = int(body.get("count", 20))
    seed = body.get("seed", None)
    raw_dist = body.get("distribution", None)

    # --- Build distribution ---
    if raw_dist is not None:
        # Caller supplied explicit counts — validate keys
        unknown_keys = set(raw_dist) - set(ALL_SCENARIOS)
        if unknown_keys:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown scenario key(s) in distribution: {sorted(unknown_keys)}. "
                       f"Valid keys: {ALL_SCENARIOS}",
            )
        distribution = {k: int(v) for k, v in raw_dist.items()}
    else:
        # Scale DEFAULT_DISTRIBUTION proportionally to requested count
        total_default = sum(DEFAULT_DISTRIBUTION.values())  # 100
        distribution = {}
        allocated = 0
        items = list(DEFAULT_DISTRIBUTION.items())
        for i, (scenario, default_count) in enumerate(items):
            if i == len(items) - 1:
                # Last scenario gets the remainder to avoid rounding gaps
                distribution[scenario] = count - allocated
            else:
                share = round(default_count / total_default * count)
                distribution[scenario] = share
                allocated += share
        # Clamp negatives that rounding can produce for tiny counts
        distribution = {k: max(0, v) for k, v in distribution.items()}

    # --- Generate + process ---
    try:
        gen = SyntheticInvoiceGenerator(seed=seed)
        invoices = gen.generate(distribution=distribution)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        with BatchProcessor() as processor:
            results = processor.process(invoices)
    except Exception as exc:
        logger.error(f"Batch processing error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch processing failed: {exc}")

    report = EvaluationService().evaluate(results)
    return report.to_dict()


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
