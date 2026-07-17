import os
import logging
import traceback
import requests as http_requests
import time
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from sqlalchemy.exc import SQLAlchemyError

from schemas.invoice_schema import InvoiceExtraction
from services.matching import MatchingEngine
from services.audit_service import AuditService
from repositories.invoice_repo import InvoiceRepository
from database import SessionLocal

load_dotenv()
logger = logging.getLogger("ap_agent.nodes")

# ---------------------------------------------------------------------------
# DIAGNOSTIC: API key check at module load time
# ---------------------------------------------------------------------------
_groq_key = os.getenv("GROQ_API_KEY", "").strip()
if not _groq_key:
    logger.error("DIAG: GROQ_API_KEY is NOT set in environment.")
else:
    logger.info(
        f"DIAG: GROQ_API_KEY loaded. "
        f"Length={len(_groq_key)}, "
        f"Prefix={_groq_key[:7]}{'*' * (len(_groq_key) - 7)}"
    )

# ---------------------------------------------------------------------------
# DIAGNOSTIC: Outbound connectivity probe at module load time
# ---------------------------------------------------------------------------
def _probe_groq_connectivity() -> None:
    url = "https://api.groq.com"
    try:
        t0 = time.time()
        r = http_requests.get(url, timeout=15)
        elapsed = round(time.time() - t0, 2)
        logger.info(
            f"DIAG: Connectivity probe to {url} → "
            f"status={r.status_code}, elapsed={elapsed}s, "
            f"body_preview={r.text[:200]!r}"
        )
    except http_requests.exceptions.ConnectionError as exc:
        logger.error(
            f"DIAG: Connectivity probe FAILED — ConnectionError: {exc}\n"
            f"Full traceback:\n{traceback.format_exc()}"
        )
    except http_requests.exceptions.Timeout as exc:
        logger.error(
            f"DIAG: Connectivity probe FAILED — Timeout after 15s: {exc}\n"
            f"Full traceback:\n{traceback.format_exc()}"
        )
    except Exception as exc:
        logger.error(
            f"DIAG: Connectivity probe FAILED — {type(exc).__name__}: {exc}\n"
            f"Full traceback:\n{traceback.format_exc()}"
        )

_probe_groq_connectivity()

llm = ChatGroq(
    model="llama-3.3-70b-versatile",
    api_key=_groq_key,
)


# ---------------------------------------------------------------------------
# Helper: write one audit record.
# Skips silently when invoice_id == 0 (test / pre-save runs).
# ---------------------------------------------------------------------------

def _audit(invoice_id: int, agent_name: str, decision: str, reasoning: str) -> None:
    if not invoice_id:
        return
    db = SessionLocal()
    try:
        repo = InvoiceRepository(db)
        AuditService(repo).log_step(
            invoice_id=invoice_id,
            agent_name=agent_name,
            decision=decision,
            reasoning=reasoning,
        )
    except SQLAlchemyError as exc:
        logger.warning(f"Audit write failed for invoice {invoice_id}: {exc}")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Node 1: Extraction
# ---------------------------------------------------------------------------

async def extraction_node(state: dict) -> dict:
    """
    Uses the LLM to extract structured invoice data from raw OCR text.
    On failure, extracted_data is left empty for validation_node to catch.

    If extracted_data is already populated (e.g. pre-seeded in tests),
    the LLM call is skipped entirely.
    """
    invoice_id = state.get("invoice_id", 0)

    # --- Short-circuit: data already provided (test / pre-seeded runs) ---
    if state.get("extracted_data"):
        reasoning_entry = "Extraction skipped (pre-seeded data detected)."
        _audit(invoice_id, "extraction_node", "SKIPPED_PRE_SEEDED", reasoning_entry)
        return {
            "reasoning": state.get("reasoning", []) + [reasoning_entry],
        }

    prompt = f"Extract invoice details into JSON: {state['raw_text']}"
    structured_llm = llm.with_structured_output(InvoiceExtraction)

    try:
        response = structured_llm.invoke(prompt)
        extracted = response.dict()
        reasoning_entry = "Extraction successful"
        decision = "EXTRACTED"
        result = {
            "extracted_data": extracted,
            "reasoning": state.get("reasoning", []) + [reasoning_entry],
        }
    except Exception as exc:
        reasoning_entry = f"Extraction failed: {type(exc).__name__}: {exc}"
        decision = "EXTRACTION_ERROR"
        logger.exception(
            f"DIAG: extraction_node FULL TRACEBACK for invoice {invoice_id}. "
            f"Exception type: {type(exc).__name__}. "
            f"Exception args: {exc.args}. "
            f"__cause__: {exc.__cause__}. "
            f"__context__: {exc.__context__}."
        )
        result = {
            "extracted_data": {},
            "reasoning": state.get("reasoning", []) + [reasoning_entry],
        }

    _audit(invoice_id, "extraction_node", decision, reasoning_entry)
    return result


# ---------------------------------------------------------------------------
# Node 2: Validation
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = ["vendor_name", "po_number", "total_amount", "line_items"]


async def validation_node(state: dict) -> dict:
    """
    Validates that extraction produced a complete, non-empty result.
    On failure: sets status = EXTRACTION_FAILED.
    On success: leaves status unchanged.
    """
    invoice_id = state.get("invoice_id", 0)
    data = state.get("extracted_data", {})
    reasoning = state.get("reasoning", [])
    exceptions = list(state.get("exceptions", []))

    # Empty result
    if not data:
        reason = (
            "Invoice extraction returned no data. "
            "The document may be unreadable or unsupported."
        )
        _audit(invoice_id, "validation_node", "EXTRACTION_FAILED", reason)
        return {
            "status": "EXTRACTION_FAILED",
            "exceptions": exceptions + [{"type": "EXTRACTION_FAILED", "description": reason}],
            "reasoning": reasoning + [f"Validation failed: {reason}"],
        }

    # Required fields missing
    missing = [f for f in REQUIRED_FIELDS if not data.get(f)]
    if missing:
        reason = (
            f"Required field(s) missing after extraction: {', '.join(missing)}. "
            "Values must not be guessed."
        )
        _audit(invoice_id, "validation_node", "MISSING_REQUIRED_FIELD", reason)
        return {
            "status": "EXTRACTION_FAILED",
            "exceptions": exceptions + [{"type": "MISSING_REQUIRED_FIELD", "description": reason}],
            "reasoning": reasoning + [f"Validation failed: {reason}"],
        }

    # line_items must be a non-empty list
    line_items = data.get("line_items", [])
    if not isinstance(line_items, list) or len(line_items) == 0:
        reason = "Invoice must contain at least one line item."
        _audit(invoice_id, "validation_node", "MISSING_REQUIRED_FIELD", reason)
        return {
            "status": "EXTRACTION_FAILED",
            "exceptions": exceptions + [{"type": "MISSING_REQUIRED_FIELD", "description": reason}],
            "reasoning": reasoning + [f"Validation failed: {reason}"],
        }

    reason = "All required fields present and non-empty."
    _audit(invoice_id, "validation_node", "VALIDATION_PASSED", reason)
    return {
        "reasoning": reasoning + [f"Validation passed: {reason}"],
    }


# ---------------------------------------------------------------------------
# Node 3: Matching
# ---------------------------------------------------------------------------

async def matching_node(state: dict) -> dict:
    """
    Fetches PO and Contract from the DB and runs MatchingEngine checks.
    Accumulates exceptions. DB errors are caught and surfaced as exceptions
    rather than crashing the pipeline.
    """
    if state.get("status") == "EXTRACTION_FAILED":
        return {}

    invoice_id = state.get("invoice_id", 0)
    data = state["extracted_data"]
    reasoning = list(state.get("reasoning", []))
    exceptions = list(state.get("exceptions", []))

    db = SessionLocal()
    try:
        repo = InvoiceRepository(db)

        # --- PO Matching ---
        po_number = data.get("po_number")
        if po_number:
            try:
                po = repo.get_po(po_number)
            except SQLAlchemyError as exc:
                desc = f"Database error looking up PO '{po_number}': {exc}"
                exceptions.append({"type": "DB_ERROR", "description": desc})
                reasoning.append(desc)
                logger.error(desc)
                po = None

            if po is None and not any(e["type"] == "DB_ERROR" for e in exceptions):
                desc = f"Purchase Order '{po_number}' not found in the system."
                exceptions.append({"type": "UNKNOWN_PO", "description": desc})
                reasoning.append(f"PO lookup failed: '{po_number}' not found")
                _audit(invoice_id, "matching_node", "UNKNOWN_PO", desc)
            elif po is not None:
                reasoning.append(f"PO '{po_number}' found. Running line-item comparison.")
                po_items = po.line_items if po.line_items else []
                if po_items:
                    mismatches = MatchingEngine.compare_line_items(
                        data.get("line_items", []), po_items
                    )
                    for mismatch in mismatches:
                        exceptions.append({"type": "PO_MISMATCH", "description": mismatch})
                    summary = (
                        f"{len(mismatches)} mismatch(es) found"
                        if mismatches
                        else "all line items match"
                    )
                    reasoning.append(f"PO matching: {summary}")
                    _audit(invoice_id, "matching_node", "PO_MATCH_COMPLETE", summary)
                else:
                    note = f"PO '{po_number}' has no line items stored — skipping item comparison."
                    reasoning.append(note)
                    _audit(invoice_id, "matching_node", "PO_NO_LINE_ITEMS", note)
        else:
            desc = "Invoice does not reference a Purchase Order number."
            exceptions.append({"type": "MISSING_PO", "description": desc})
            reasoning.append("Matching skipped: no PO number on invoice")
            _audit(invoice_id, "matching_node", "MISSING_PO", desc)

        # --- Contract Compliance ---
        contract_number = data.get("contract_number")
        if contract_number:
            try:
                contract = repo.get_contract(contract_number)
            except SQLAlchemyError as exc:
                desc = f"Database error looking up contract '{contract_number}': {exc}"
                exceptions.append({"type": "DB_ERROR", "description": desc})
                reasoning.append(desc)
                logger.error(desc)
                contract = None

            if contract is None and not any(e["type"] == "DB_ERROR" for e in exceptions):
                desc = f"Contract '{contract_number}' not found in the system."
                exceptions.append({"type": "UNKNOWN_CONTRACT", "description": desc})
                reasoning.append(f"Contract lookup failed: '{contract_number}' not found")
                _audit(invoice_id, "matching_node", "UNKNOWN_CONTRACT", desc)
            elif contract is not None:
                reasoning.append(f"Contract '{contract_number}' found. Checking compliance.")
                issue = MatchingEngine.check_contract_compliance(data, contract)
                if issue:
                    exceptions.append({"type": "CONTRACT_VIOLATION", "description": issue})
                    reasoning.append(f"Contract compliance failed: {issue}")
                    _audit(invoice_id, "matching_node", "CONTRACT_VIOLATION", issue)
                else:
                    note = "Contract compliance passed."
                    reasoning.append(note)
                    _audit(invoice_id, "matching_node", "CONTRACT_OK", note)
        else:
            reasoning.append("No contract number on invoice — contract compliance skipped.")

    except Exception as exc:
        # Catch-all so a DB crash never silently produces a STRAIGHT_THROUGH result
        desc = f"Unexpected error in matching_node: {exc}"
        exceptions.append({"type": "MATCHING_ERROR", "description": desc})
        reasoning.append(desc)
        logger.error(desc)
    finally:
        db.close()

    return {"exceptions": exceptions, "reasoning": reasoning}


# ---------------------------------------------------------------------------
# Node 4: Decision
# ---------------------------------------------------------------------------

async def decision_node(state: dict) -> dict:
    """
    Reads exceptions list.
    No exceptions → STRAIGHT_THROUGH (payment scheduled).
    Any exception  → EXCEPTION (routed to AP clerk).
    """
    invoice_id = state.get("invoice_id", 0)
    exceptions = state.get("exceptions", [])
    reasoning = list(state.get("reasoning", []))

    # Respect earlier EXTRACTION_FAILED
    if state.get("status") == "EXTRACTION_FAILED":
        reason = "Decision: EXCEPTION (extraction failed — cannot process invoice)"
        _audit(invoice_id, "decision_node", "EXCEPTION", reason)
        return {"status": "EXCEPTION", "reasoning": reasoning + [reason]}

    if exceptions:
        reasons = "; ".join(e["description"] for e in exceptions)
        summary = f"Decision: EXCEPTION — {len(exceptions)} issue(s): {reasons}"
        _audit(invoice_id, "decision_node", "EXCEPTION", summary)
        return {"status": "EXCEPTION", "reasoning": reasoning + [summary]}

    summary = "Decision: STRAIGHT_THROUGH — all checks passed. Payment scheduled."
    _audit(invoice_id, "decision_node", "STRAIGHT_THROUGH", summary)
    return {"status": "STRAIGHT_THROUGH", "reasoning": reasoning + [summary]}
