"""
EvaluationService
=================
Compares ground-truth expectations against actual batch-processing results
and produces a structured, serialisable EvaluationReport.

Design principles
-----------------
* NEVER fabricates accuracy values.  If ground truth or actual result is
  missing, the corresponding metric is set to None with a clear field name
  (e.g. decision_accuracy=None).

* All monetary arithmetic uses round(x, 2) to stay consistent with the
  existing project conventions (Float columns, USD amounts).

* The report is a dataclass with a to_dict() method so it can be returned
  directly from a FastAPI endpoint or printed as JSON.

Accuracy definitions
--------------------
decision_accuracy:
    Fraction of successfully-processed invoices where the actual pipeline
    decision matches the ground-truth expected decision.
    Formula: correct_decisions / successfully_processed
    Unavailable when successfully_processed == 0.

exception_type_accuracy:
    Among invoices where the ground truth expects a specific exception type,
    what fraction actually produced that exception type.
    Formula: correct_exception_types / invoices_with_expected_exception_type
    Unavailable when no ground-truth exception types exist.

Both metrics require ground truth — they are only computed for synthetic
batches.  For real (non-synthetic) batches these fields remain None.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from batch.batch_processor import BatchResult


# ---------------------------------------------------------------------------
# Report dataclass
# ---------------------------------------------------------------------------

@dataclass
class EvaluationReport:
    # ---- Volume ----
    total_invoices: int
    successfully_processed: int
    processing_failures: int

    # ---- Pipeline decisions ----
    exceptions_raised: int          # invoices that got status EXCEPTION
    automatically_cleared: int      # status STRAIGHT_THROUGH
    extraction_failed: int          # status EXTRACTION_FAILED (counted separately)

    # ---- Rates ----
    match_rate: Optional[float]      # auto-cleared / total  (None if total == 0)
    exception_rate: Optional[float]  # exceptions / total    (None if total == 0)

    # ---- Exception breakdown ----
    po_mismatch_count: int
    quantity_mismatch_count: int
    unknown_po_count: int
    contract_violation_count: int
    duplicate_invoice_count: int
    possible_duplicate_count: int
    extraction_failure_exc_count: int   # EXTRACTION_FAILED exception rows
    other_exception_count: int

    # ---- Financial metrics ----
    total_invoice_value: float
    auto_cleared_value: float
    exception_value: float
    pending_review_value: float
    duplicate_invoice_value: float

    # ---- Accuracy (only available when ground truth present) ----
    decision_accuracy: Optional[float]       # None if unavailable
    exception_type_accuracy: Optional[float] # None if unavailable

    # ---- Per-scenario ground-truth breakdown ----
    scenario_counts: Dict[str, int] = field(default_factory=dict)
    scenario_correct: Dict[str, int] = field(default_factory=dict)

    # ---- Processing performance ----
    avg_processing_time_ms: Optional[float] = None
    total_processing_time_ms: float = 0.0

    def to_dict(self) -> dict:
        """Return a plain dict suitable for JSON serialisation."""
        return {
            # Volume
            "total_invoices": self.total_invoices,
            "successfully_processed": self.successfully_processed,
            "processing_failures": self.processing_failures,
            # Decisions
            "exceptions_raised": self.exceptions_raised,
            "automatically_cleared": self.automatically_cleared,
            "extraction_failed": self.extraction_failed,
            # Rates
            "match_rate": self.match_rate,
            "exception_rate": self.exception_rate,
            # Exception breakdown
            "exception_breakdown": {
                "po_mismatch": self.po_mismatch_count,
                "quantity_mismatch": self.quantity_mismatch_count,
                "unknown_po": self.unknown_po_count,
                "contract_violation": self.contract_violation_count,
                "duplicate_invoice": self.duplicate_invoice_count,
                "possible_duplicate": self.possible_duplicate_count,
                "extraction_failure": self.extraction_failure_exc_count,
                "other": self.other_exception_count,
            },
            # Financial
            "financial": {
                "total_invoice_value": self.total_invoice_value,
                "auto_cleared_value": self.auto_cleared_value,
                "exception_value": self.exception_value,
                "pending_review_value": self.pending_review_value,
                "duplicate_invoice_value": self.duplicate_invoice_value,
            },
            # Accuracy
            "accuracy": {
                "decision_accuracy": self.decision_accuracy,
                "exception_type_accuracy": self.exception_type_accuracy,
            },
            # Scenario breakdown
            "scenario_counts": self.scenario_counts,
            "scenario_correct": self.scenario_correct,
            # Performance
            "performance": {
                "avg_processing_time_ms": self.avg_processing_time_ms,
                "total_processing_time_ms": self.total_processing_time_ms,
            },
        }

    def format_report(self, currency_symbol: str = "$") -> str:
        """
        Return a human-readable text report suitable for console output or
        demo display.
        """
        def _pct(v: Optional[float]) -> str:
            return f"{v * 100:.1f}%" if v is not None else "N/A"

        def _money(v: float) -> str:
            return f"{currency_symbol}{v:,.2f}"

        lines = [
            "",
            "=" * 52,
            "  AI FINANCE CONTROLLER -- BATCH EVALUATION REPORT",
            "=" * 52,
            f"  Invoices processed:        {self.total_invoices:>6}",
            f"  Successfully processed:    {self.successfully_processed:>6}",
            f"  Processing failures:       {self.processing_failures:>6}",
            "-" * 52,
            f"  Automatically cleared:     {self.automatically_cleared:>6}  ({_pct(self.match_rate)})",
            f"  Exceptions raised:         {self.exceptions_raised:>6}  ({_pct(self.exception_rate)})",
            f"  Extraction failed:         {self.extraction_failed:>6}",
            "-" * 52,
            "  Exception breakdown:",
            f"    PO mismatches:           {self.po_mismatch_count:>6}",
            f"    Quantity mismatches:     {self.quantity_mismatch_count:>6}",
            f"    Unknown PO:              {self.unknown_po_count:>6}",
            f"    Contract violations:     {self.contract_violation_count:>6}",
            f"    Duplicate invoices:      {self.duplicate_invoice_count:>6}",
            f"    Possible duplicates:     {self.possible_duplicate_count:>6}",
            f"    Extraction failures:     {self.extraction_failure_exc_count:>6}",
            f"    Other:                   {self.other_exception_count:>6}",
            "-" * 52,
            "  Financial summary:",
            f"    Total invoice value:     {_money(self.total_invoice_value):>14}",
            f"    Auto-cleared value:      {_money(self.auto_cleared_value):>14}",
            f"    Exception value:         {_money(self.exception_value):>14}",
            f"    Duplicate value:         {_money(self.duplicate_invoice_value):>14}",
            "-" * 52,
            "  Accuracy (ground truth):",
            f"    Decision accuracy:       {_pct(self.decision_accuracy):>8}",
            f"    Exception type accuracy: {_pct(self.exception_type_accuracy):>8}",
            "=" * 52,
            "",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class EvaluationService:
    """
    Stateless service.  Call evaluate() with a list of BatchResult objects.
    """

    # Exception type string constants (same values as used by the pipeline)
    _PO_MISMATCH = "PO_MISMATCH"
    _UNKNOWN_PO = "UNKNOWN_PO"
    _CONTRACT_VIOLATION = "CONTRACT_VIOLATION"
    _DUPLICATE_INVOICE = "DUPLICATE_INVOICE"
    _POSSIBLE_DUPLICATE = "POSSIBLE_DUPLICATE"
    _EXTRACTION_FAILED = "EXTRACTION_FAILED"
    _MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"

    def evaluate(self, results: List[BatchResult]) -> EvaluationReport:
        """
        Compute and return an EvaluationReport for a completed batch.

        Parameters
        ----------
        results: list of BatchResult from BatchProcessor.process()

        Returns
        -------
        EvaluationReport with all metrics populated.
        """
        if not results:
            return self._empty_report()

        total = len(results)
        successful = [r for r in results if r.success]
        failed = [r for r in results if not r.success]

        # ---- Decision counts ----
        cleared = [r for r in successful if r.actual_decision == "STRAIGHT_THROUGH"]
        exceptions = [r for r in successful if r.actual_decision in ("EXCEPTION", "EXTRACTION_FAILED")]
        # Extraction failures are a sub-type of EXCEPTION in the pipeline;
        # count them separately for clarity.
        extraction_failed_decisions = [r for r in successful if r.actual_decision == "EXTRACTION_FAILED"]

        # ---- Exception type breakdown ----
        # Count across all exception rows produced
        exc_type_counts = self._count_exception_types(successful)

        # ---- Financial metrics ----
        total_value = round(sum(r.total_amount for r in results), 2)
        cleared_value = round(sum(r.total_amount for r in cleared), 2)
        exc_value = round(sum(r.total_amount for r in exceptions), 2)
        pending_value = round(sum(r.total_amount for r in failed), 2)
        dup_value = round(
            sum(
                r.total_amount for r in successful
                if any(
                    e.get("type") in (self._DUPLICATE_INVOICE, self._POSSIBLE_DUPLICATE)
                    for e in r.actual_exceptions
                )
            ),
            2,
        )

        # ---- Rates ----
        match_rate = round(len(cleared) / total, 4) if total > 0 else None
        exc_rate = round(len(exceptions) / total, 4) if total > 0 else None

        # ---- Accuracy ----
        decision_accuracy = self._decision_accuracy(successful)
        exception_type_accuracy = self._exception_type_accuracy(successful)

        # ---- Per-scenario breakdown ----
        scenario_counts: Dict[str, int] = {}
        scenario_correct: Dict[str, int] = {}
        for r in results:
            sc = r.scenario
            scenario_counts[sc] = scenario_counts.get(sc, 0) + 1
            if r.success and r.actual_decision == r.ground_truth_decision:
                scenario_correct[sc] = scenario_correct.get(sc, 0) + 1

        # ---- Performance ----
        times = [r.processing_time_ms for r in results if r.processing_time_ms > 0]
        total_time = round(sum(times), 2)
        avg_time = round(total_time / len(times), 2) if times else None

        return EvaluationReport(
            total_invoices=total,
            successfully_processed=len(successful),
            processing_failures=len(failed),
            exceptions_raised=len(exceptions),
            automatically_cleared=len(cleared),
            extraction_failed=len(extraction_failed_decisions),
            match_rate=match_rate,
            exception_rate=exc_rate,
            # Exception breakdown
            po_mismatch_count=exc_type_counts.get(self._PO_MISMATCH, 0),
            quantity_mismatch_count=0,   # Not a distinct exception type; PO_MISMATCH covers qty
            unknown_po_count=exc_type_counts.get(self._UNKNOWN_PO, 0),
            contract_violation_count=exc_type_counts.get(self._CONTRACT_VIOLATION, 0),
            duplicate_invoice_count=exc_type_counts.get(self._DUPLICATE_INVOICE, 0),
            possible_duplicate_count=exc_type_counts.get(self._POSSIBLE_DUPLICATE, 0),
            extraction_failure_exc_count=(
                exc_type_counts.get(self._EXTRACTION_FAILED, 0)
                + exc_type_counts.get(self._MISSING_REQUIRED_FIELD, 0)
            ),
            other_exception_count=self._other_count(exc_type_counts),
            # Financial
            total_invoice_value=total_value,
            auto_cleared_value=cleared_value,
            exception_value=exc_value,
            pending_review_value=pending_value,
            duplicate_invoice_value=dup_value,
            # Accuracy
            decision_accuracy=decision_accuracy,
            exception_type_accuracy=exception_type_accuracy,
            # Scenario breakdown
            scenario_counts=scenario_counts,
            scenario_correct=scenario_correct,
            # Performance
            avg_processing_time_ms=avg_time,
            total_processing_time_ms=total_time,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _empty_report() -> EvaluationReport:
        return EvaluationReport(
            total_invoices=0,
            successfully_processed=0,
            processing_failures=0,
            exceptions_raised=0,
            automatically_cleared=0,
            extraction_failed=0,
            match_rate=None,
            exception_rate=None,
            po_mismatch_count=0,
            quantity_mismatch_count=0,
            unknown_po_count=0,
            contract_violation_count=0,
            duplicate_invoice_count=0,
            possible_duplicate_count=0,
            extraction_failure_exc_count=0,
            other_exception_count=0,
            total_invoice_value=0.0,
            auto_cleared_value=0.0,
            exception_value=0.0,
            pending_review_value=0.0,
            duplicate_invoice_value=0.0,
            decision_accuracy=None,
            exception_type_accuracy=None,
        )

    @staticmethod
    def _count_exception_types(successful: List[BatchResult]) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for r in successful:
            for exc in r.actual_exceptions:
                t = exc.get("type", "UNKNOWN")
                counts[t] = counts.get(t, 0) + 1
        return counts

    @classmethod
    def _other_count(cls, exc_type_counts: Dict[str, int]) -> int:
        known = {
            cls._PO_MISMATCH, cls._UNKNOWN_PO, cls._CONTRACT_VIOLATION,
            cls._DUPLICATE_INVOICE, cls._POSSIBLE_DUPLICATE,
            cls._EXTRACTION_FAILED, cls._MISSING_REQUIRED_FIELD,
            "MISSING_PO", "UNKNOWN_CONTRACT", "DB_ERROR", "MATCHING_ERROR",
        }
        return sum(v for k, v in exc_type_counts.items() if k not in known)

    @staticmethod
    def _decision_accuracy(successful: List[BatchResult]) -> Optional[float]:
        """
        Fraction of successfully-processed invoices where actual decision
        matches ground truth.  Returns None if no successful results.
        """
        if not successful:
            return None
        correct = sum(
            1 for r in successful
            if r.actual_decision == r.ground_truth_decision
        )
        return round(correct / len(successful), 4)

    @staticmethod
    def _exception_type_accuracy(successful: List[BatchResult]) -> Optional[float]:
        """
        Among invoices where ground truth specifies an exception type, what
        fraction actually produced that exception type (anywhere in the
        actual_exceptions list).  Returns None if no such invoices.
        """
        candidates = [
            r for r in successful
            if r.ground_truth_exception_type is not None
        ]
        if not candidates:
            return None
        correct = sum(
            1 for r in candidates
            if any(
                e.get("type") == r.ground_truth_exception_type
                for e in r.actual_exceptions
            )
        )
        return round(correct / len(candidates), 4)
