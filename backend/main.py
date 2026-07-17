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
# DIAGNOSTIC: connectivity + env check (REMOVE after root cause confirmed)
# ---------------------------------------------------------------------------

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

        # --- Duplicate invoice number detection ---
        if extracted_invoice_number:
            existing = (
                db.query(Invoice)
                .filter(
                    Invoice.invoice_number == extracted_invoice_number,
                    Invoice.id != invoice_id,
                )
                .first()
            )
            if existing:
                try:
                    existing_status = existing.status.value
                except Exception:
                    existing_status = str(existing.status)
                exceptions_list.append({
                    "type": "DUPLICATE_INVOICE",
                    "description": (
                        f"Invoice number '{extracted_invoice_number}' was already submitted "
                        f"(existing record ID: {existing.id}, status: {existing_status}). "
                        "Duplicate invoices must be reviewed manually."
                    ),
                })
                agent_status = "EXCEPTION"
                db_status = InvoiceStatus.REVIEW_REQUIRED
                logger.warning(
                    f"Duplicate '{extracted_invoice_number}' detected "
                    f"(new id={invoice_id}, existing id={existing.id})"
                )

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


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
