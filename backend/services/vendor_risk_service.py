"""
VendorRiskService
=================
Calculates a deterministic, explainable risk score (0–100) for a vendor based
on historical invoice and exception data already stored in the database.

No LLM calls. No external APIs. No new database technology.

Scoring formula
---------------
The score is the sum of six independent signals, capped at 100.

Signal 1 — Overall exception rate          max 40 pts
    Measures what fraction of the vendor's processed invoices raised at least
    one exception.  Tiered (higher tier replaces lower):

      exception_rate ≥ 50%  →  40 pts
      exception_rate ≥ 25%  →  25 pts
      exception_rate ≥ 10%  →  15 pts
      otherwise             →   0 pts

Signal 2 — Duplicate invoice history        max 20 pts
    Counts DUPLICATE_INVOICE and POSSIBLE_DUPLICATE exceptions (from Task 1).

      duplicate_count ≥ 3   →  20 pts
      duplicate_count ≥ 1   →  10 pts
      otherwise             →   0 pts

Signal 3 — PO mismatch frequency            max 20 pts
    Counts PO_MISMATCH exceptions.

      po_mismatch_count ≥ 3 →  20 pts
      po_mismatch_count ≥ 1 →  10 pts
      otherwise             →   0 pts

Signal 4 — Contract violations              max 25 pts
    Counts CONTRACT_VIOLATION exceptions.

      contract_violation_count ≥ 3 →  25 pts
      contract_violation_count ≥ 1 →  15 pts
      otherwise                    →   0 pts

Signal 5 — Extraction failures              max 10 pts
    Counts EXTRACTION_FAILED and MISSING_REQUIRED_FIELD exceptions.
    These may indicate systematic document quality problems with the vendor.

      extraction_failure_count ≥ 3 →  10 pts
      extraction_failure_count ≥ 1 →   5 pts
      otherwise                    →   0 pts

Signal 6 — Recent exception concentration   max 10 pts
    If ≥ 50% of invoices submitted in the last 30 days raised exceptions,
    add a recency penalty.  This catches a vendor whose behaviour is
    deteriorating, even if their overall historical rate is acceptable.

      recent_exception_rate ≥ 50% (with ≥ 2 recent invoices)  →  10 pts
      otherwise                                                 →   0 pts

Maximum possible raw score: 40+20+20+25+10+10 = 125 → capped to 100.

Risk levels
-----------
    0–29   LOW
    30–59  MEDIUM
    60–79  HIGH
    80–100 CRITICAL

Usage
-----
    repo = InvoiceRepository(db)
    svc  = VendorRiskService(repo)
    report = svc.calculate(vendor_id=3)
    print(report.risk_score, report.risk_level, report.risk_factors)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List

from repositories.invoice_repo import InvoiceRepository

# Reuse exception type constants from Task 1 — do NOT re-declare them here.
from services.duplicate_service import EXACT_DUPLICATE_TYPE, POSSIBLE_DUPLICATE_TYPE

logger = logging.getLogger("ap_agent.vendor_risk_service")

# Exception type strings used in scoring
_PO_MISMATCH = "PO_MISMATCH"
_CONTRACT_VIOLATION = "CONTRACT_VIOLATION"
_EXTRACTION_FAILED = "EXTRACTION_FAILED"
_MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"

# Risk level thresholds
_THRESHOLDS = [
    (80, "CRITICAL"),
    (60, "HIGH"),
    (30, "MEDIUM"),
    (0,  "LOW"),
]


def _risk_level(score: int) -> str:
    for threshold, level in _THRESHOLDS:
        if score >= threshold:
            return level
    return "LOW"


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class VendorRiskReport:
    """
    Structured, JSON-serialisable result of a vendor risk calculation.

    All fields are plain Python primitives so the report can be returned
    directly from a FastAPI endpoint without a Pydantic model adapter.
    """
    vendor_id: int
    vendor_name: str
    vendor_code: str
    risk_score: int                      # 0–100
    risk_level: str                      # LOW / MEDIUM / HIGH / CRITICAL

    # Raw counts
    total_invoices: int
    total_exceptions: int
    exception_rate: float                # 0.0–1.0

    # Per-signal counts
    duplicate_count: int                 # DUPLICATE_INVOICE + POSSIBLE_DUPLICATE
    po_mismatch_count: int
    contract_violation_count: int
    extraction_failure_count: int

    # Recent activity (last 30 days)
    recent_invoices: int
    recent_exceptions: int
    recent_exception_rate: float         # 0.0–1.0

    # Score breakdown — one entry per signal that contributed points
    score_breakdown: List[dict] = field(default_factory=list)

    # Human-readable explanation of why the score is what it is
    risk_factors: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Return a plain dict suitable for a JSON API response."""
        return {
            "vendor_id": self.vendor_id,
            "vendor_name": self.vendor_name,
            "vendor_code": self.vendor_code,
            "risk_score": self.risk_score,
            "risk_level": self.risk_level,
            "total_invoices": self.total_invoices,
            "total_exceptions": self.total_exceptions,
            "exception_rate": round(self.exception_rate, 4),
            "duplicate_count": self.duplicate_count,
            "po_mismatch_count": self.po_mismatch_count,
            "contract_violation_count": self.contract_violation_count,
            "extraction_failure_count": self.extraction_failure_count,
            "recent_invoices": self.recent_invoices,
            "recent_exceptions": self.recent_exceptions,
            "recent_exception_rate": round(self.recent_exception_rate, 4),
            "score_breakdown": self.score_breakdown,
            "risk_factors": self.risk_factors,
        }


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class VendorRiskService:
    """
    Stateless service — receives an InvoiceRepository on construction so it
    can be injected with a test repository without patching globals.
    """

    def __init__(self, repo: InvoiceRepository) -> None:
        self._repo = repo

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def calculate(self, vendor_id: int) -> VendorRiskReport:
        """
        Calculate and return a VendorRiskReport for the given vendor_id.

        Raises ValueError if the vendor does not exist in the database.
        """
        vendor = self._repo.get_vendor_by_id(vendor_id)
        if vendor is None:
            raise ValueError(f"Vendor {vendor_id} not found")

        # --- Raw data from DB ---
        invoices = self._repo.get_invoices_for_vendor(vendor_id)
        exc_counts = self._repo.get_exception_counts_for_vendor(vendor_id)
        recent_inv, recent_exc = self._repo.get_recent_invoice_and_exception_counts(vendor_id)

        total_invoices = len(invoices)
        total_exceptions = sum(exc_counts.values())

        # Per-type counts
        duplicate_count = (
            exc_counts.get(EXACT_DUPLICATE_TYPE, 0)
            + exc_counts.get(POSSIBLE_DUPLICATE_TYPE, 0)
        )
        po_mismatch_count = exc_counts.get(_PO_MISMATCH, 0)
        contract_violation_count = exc_counts.get(_CONTRACT_VIOLATION, 0)
        extraction_failure_count = (
            exc_counts.get(_EXTRACTION_FAILED, 0)
            + exc_counts.get(_MISSING_REQUIRED_FIELD, 0)
        )

        # Rates
        exception_rate = total_exceptions / total_invoices if total_invoices > 0 else 0.0
        recent_exception_rate = recent_exc / recent_inv if recent_inv > 0 else 0.0

        # --- Scoring ---
        score_breakdown: List[dict] = []
        factors: List[str] = []
        raw_score = 0

        raw_score, score_breakdown, factors = self._apply_signals(
            exception_rate=exception_rate,
            duplicate_count=duplicate_count,
            po_mismatch_count=po_mismatch_count,
            contract_violation_count=contract_violation_count,
            extraction_failure_count=extraction_failure_count,
            recent_invoices=recent_inv,
            recent_exception_rate=recent_exception_rate,
            total_invoices=total_invoices,
        )

        final_score = min(raw_score, 100)

        if total_invoices == 0:
            factors.append("No processed invoices found — insufficient data to assess risk.")

        return VendorRiskReport(
            vendor_id=vendor_id,
            vendor_name=vendor.name,
            vendor_code=vendor.vendor_code or "",
            risk_score=final_score,
            risk_level=_risk_level(final_score),
            total_invoices=total_invoices,
            total_exceptions=total_exceptions,
            exception_rate=exception_rate,
            duplicate_count=duplicate_count,
            po_mismatch_count=po_mismatch_count,
            contract_violation_count=contract_violation_count,
            extraction_failure_count=extraction_failure_count,
            recent_invoices=recent_inv,
            recent_exceptions=recent_exc,
            recent_exception_rate=recent_exception_rate,
            score_breakdown=score_breakdown,
            risk_factors=factors,
        )

    # ------------------------------------------------------------------
    # Internal scoring
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_signals(
        exception_rate: float,
        duplicate_count: int,
        po_mismatch_count: int,
        contract_violation_count: int,
        extraction_failure_count: int,
        recent_invoices: int,
        recent_exception_rate: float,
        total_invoices: int,
    ) -> tuple[int, list, list]:
        """
        Apply all six signals and return (raw_score, breakdown_list, factors_list).

        Each signal is independent — only the contribution from that signal is
        added; they do not interact.
        """
        score = 0
        breakdown: List[dict] = []
        factors: List[str] = []

        # --- Signal 1: Overall exception rate ---
        if exception_rate >= 0.50:
            pts = 40
            factors.append(
                f"Very high invoice exception rate: {exception_rate:.0%} of invoices had exceptions"
            )
        elif exception_rate >= 0.25:
            pts = 25
            factors.append(
                f"High invoice exception rate: {exception_rate:.0%} of invoices had exceptions"
            )
        elif exception_rate >= 0.10:
            pts = 15
            factors.append(
                f"Elevated invoice exception rate: {exception_rate:.0%} of invoices had exceptions"
            )
        else:
            pts = 0

        if pts:
            score += pts
            breakdown.append({"signal": "exception_rate", "points": pts})

        # --- Signal 2: Duplicate invoice history ---
        if duplicate_count >= 3:
            pts = 20
        elif duplicate_count >= 1:
            pts = 10
        else:
            pts = 0

        if pts:
            score += pts
            breakdown.append({"signal": "duplicate_history", "points": pts})
            factors.append(
                f"{duplicate_count} duplicate invoice attempt(s) detected"
            )

        # --- Signal 3: PO mismatch frequency ---
        if po_mismatch_count >= 3:
            pts = 20
        elif po_mismatch_count >= 1:
            pts = 10
        else:
            pts = 0

        if pts:
            score += pts
            breakdown.append({"signal": "po_mismatch", "points": pts})
            factors.append(
                f"{po_mismatch_count} PO mismatch(es) — prices or quantities differed from PO"
            )

        # --- Signal 4: Contract violations ---
        if contract_violation_count >= 3:
            pts = 25
        elif contract_violation_count >= 1:
            pts = 15
        else:
            pts = 0

        if pts:
            score += pts
            breakdown.append({"signal": "contract_violation", "points": pts})
            factors.append(
                f"{contract_violation_count} contract violation(s) — "
                "amounts exceeded contract limit or contract was expired"
            )

        # --- Signal 5: Extraction failures ---
        if extraction_failure_count >= 3:
            pts = 10
        elif extraction_failure_count >= 1:
            pts = 5
        else:
            pts = 0

        if pts:
            score += pts
            breakdown.append({"signal": "extraction_failures", "points": pts})
            factors.append(
                f"{extraction_failure_count} invoice(s) failed extraction — "
                "possible document quality issue"
            )

        # --- Signal 6: Recent exception concentration ---
        # Only meaningful when there are at least 2 recent invoices to form a rate.
        if recent_invoices >= 2 and recent_exception_rate >= 0.50:
            pts = 10
            score += pts
            breakdown.append({"signal": "recent_exception_concentration", "points": pts})
            factors.append(
                f"Recent activity concern: {recent_exception_rate:.0%} of invoices in the "
                f"last 30 days had exceptions ({recent_invoices} invoice(s))"
            )

        return score, breakdown, factors
