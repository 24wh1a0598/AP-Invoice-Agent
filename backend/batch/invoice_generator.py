"""
SyntheticInvoiceGenerator
=========================
Generates deterministic synthetic invoices for batch testing and demo.

Each invoice is a plain Python dataclass carrying:
  - extracted_data  : dict matching the AgentState["extracted_data"] shape
                      (same keys as InvoiceExtraction Pydantic schema)
  - ground_truth    : dict describing the expected outcome
  - scenario        : the scenario label (string enum below)

NO real LLM calls, NO OCR, NO PDF generation.
The extracted_data is injected directly into the pipeline via the
"pre-seeded data" short-circuit already present in extraction_node.

Reference data seeded into the test DB by BatchProcessor
---------------------------------------------------------
Three vendors, each with their own PO and contract, are seeded so that
clean invoices across vendors naturally carry different amounts.

Vendor A : "Acme Supplies"   / ACME-001  / PO-BATCH-A / CTR-BATCH-A
           Widget x10 @ $100 = $1,000  |  contract max $15,000
Vendor B : "Beta Components" / BETA-001  / PO-BATCH-B / CTR-BATCH-B
           Gadget x5  @ $250 = $1,250  |  contract max $15,000
Vendor C : "Gamma Corp"      / GAMMA-001 / PO-BATCH-C / CTR-BATCH-C
           Device x4  @ $500 = $2,000  |  contract max $15,000

CLEAN invoice uniqueness strategy
----------------------------------
Each clean invoice is assigned to one of the three vendors in round-robin
order, giving it one of three distinct base totals ($1,000 / $1,250 / $2,000).
On top of that, a small per-invoice tax amount (seeded-random, range $5–$99.99)
is added, making every total unique while keeping it well under the contract
limit and passing PO matching exactly.

Why not vary line-item quantities?
The existing MatchingEngine.compare_line_items() iterates over INVOICE items
and compares each against the PO item of the same description, checking both
unit_price AND quantity.  An invoice quantity that differs from the PO quantity
raises PO_MISMATCH — so partial orders are NOT a valid clean scenario.

Why not vary unit prices?
Unit price variance > $0.01 raises PO_MISMATCH.

Using tax variation is the correct approach because:
  1. The Pydantic InvoiceExtraction schema accepts tax_amount >= 0.
  2. compare_line_items() does not inspect tax_amount.
  3. check_contract_compliance() checks total_amount — kept well under limit.
  4. The duplicate possible-duplicate check matches on total_amount ± $0.01,
     so unique totals prevent accidental cross-firing.

Scenario catalogue
------------------
CLEAN               All fields correct, PO matches, contract within limit.
PO_PRICE_MISMATCH   Unit price differs from PO (Vendor A).
QUANTITY_MISMATCH   Quantity differs from PO (Vendor A).
UNKNOWN_PO          PO number not in system.
CONTRACT_VIOLATION  Total exceeds contract max_amount.
DUPLICATE           Same invoice number as a previously processed invoice.
EXTRACTION_FAILURE  extracted_data is empty (simulates unreadable document).

Determinism
-----------
Pass seed= to get reproducible shuffles and tax values.  With the same seed
the generator always produces the same sequence of invoices in the same order.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Scenario constants
# ---------------------------------------------------------------------------

SCENARIO_CLEAN = "CLEAN"
SCENARIO_PO_PRICE_MISMATCH = "PO_PRICE_MISMATCH"
SCENARIO_QUANTITY_MISMATCH = "QUANTITY_MISMATCH"
SCENARIO_UNKNOWN_PO = "UNKNOWN_PO"
SCENARIO_CONTRACT_VIOLATION = "CONTRACT_VIOLATION"
SCENARIO_DUPLICATE = "DUPLICATE"
SCENARIO_EXTRACTION_FAILURE = "EXTRACTION_FAILURE"

ALL_SCENARIOS = [
    SCENARIO_CLEAN,
    SCENARIO_PO_PRICE_MISMATCH,
    SCENARIO_QUANTITY_MISMATCH,
    SCENARIO_UNKNOWN_PO,
    SCENARIO_CONTRACT_VIOLATION,
    SCENARIO_DUPLICATE,
    SCENARIO_EXTRACTION_FAILURE,
]

# Default distribution for a 100-invoice batch
DEFAULT_DISTRIBUTION: Dict[str, int] = {
    SCENARIO_CLEAN: 70,
    SCENARIO_PO_PRICE_MISMATCH: 10,
    SCENARIO_QUANTITY_MISMATCH: 5,
    SCENARIO_UNKNOWN_PO: 5,
    SCENARIO_CONTRACT_VIOLATION: 4,
    SCENARIO_DUPLICATE: 3,
    SCENARIO_EXTRACTION_FAILURE: 3,
}

# ---------------------------------------------------------------------------
# Multi-vendor reference data
# ---------------------------------------------------------------------------
# Each vendor has its own PO with a distinct product/price so clean invoices
# naturally carry different base amounts ($1,000 / $1,250 / $2,000).

_VENDORS = [
    {
        "vendor_name": "Acme Supplies",
        "vendor_code": "ACME-001",
        "po_number": "PO-BATCH-A",
        "contract_number": "CTR-BATCH-A",
        "product": "Widget",
        "quantity": 10.0,
        "unit_price": 100.0,
        "subtotal": 1_000.0,
        "contract_max": 15_000.0,
    },
    {
        "vendor_name": "Beta Components",
        "vendor_code": "BETA-001",
        "po_number": "PO-BATCH-B",
        "contract_number": "CTR-BATCH-B",
        "product": "Gadget",
        "quantity": 5.0,
        "unit_price": 250.0,
        "subtotal": 1_250.0,
        "contract_max": 15_000.0,
    },
    {
        "vendor_name": "Gamma Corp",
        "vendor_code": "GAMMA-001",
        "po_number": "PO-BATCH-C",
        "contract_number": "CTR-BATCH-C",
        "product": "Device",
        "quantity": 4.0,
        "unit_price": 500.0,
        "subtotal": 2_000.0,
        "contract_max": 15_000.0,
    },
]

# Shorthand: use Vendor A's data for all exception/mismatch scenarios so the
# existing test assertions about PO_NUMBER / VENDOR_NAME still work easily.
_VA = _VENDORS[0]

# Legacy single-vendor constants — kept for backward compatibility with tests
# that import them directly.
VENDOR_NAME = _VA["vendor_name"]
VENDOR_CODE = _VA["vendor_code"]
PO_NUMBER = _VA["po_number"]
CONTRACT_NUMBER = _VA["contract_number"]
PO_UNIT_PRICE = _VA["unit_price"]
PO_QUANTITY = _VA["quantity"]
PO_TOTAL = _VA["subtotal"]
CONTRACT_MAX = _VA["contract_max"]
INVOICE_DATE = "2024-06-15"


# ---------------------------------------------------------------------------
# Reference data helper
# ---------------------------------------------------------------------------

def reference_data() -> dict:
    """
    Returns the multi-vendor reference data that BatchProcessor must seed into
    the database before running the batch.  Centralised here so the generator
    and processor always agree on the values.

    Returns a dict with key "vendors" — a list of vendor/PO/contract configs.
    BatchProcessor iterates this list when seeding.
    """
    vendors = []
    for v in _VENDORS:
        vendors.append({
            "vendor_name": v["vendor_name"],
            "vendor_code": v["vendor_code"],
            "po_number": v["po_number"],
            "po_line_items": [
                {
                    "description": v["product"],
                    "quantity": v["quantity"],
                    "unit_price": v["unit_price"],
                    "total": v["subtotal"],
                }
            ],
            "po_total": v["subtotal"],
            "contract_number": v["contract_number"],
            "contract_max_amount": v["contract_max"],
        })
    return {"vendors": vendors}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class GroundTruth:
    scenario: str
    expected_decision: str          # "STRAIGHT_THROUGH" or "EXCEPTION"
    expected_exception_type: Optional[str] = None   # e.g. "PO_MISMATCH"
    notes: str = ""


@dataclass
class SyntheticInvoice:
    invoice_id: str                  # unique string ID (not a DB id)
    scenario: str
    extracted_data: Dict             # injected directly into AgentState
    ground_truth: GroundTruth

    @property
    def expected_decision(self) -> str:
        return self.ground_truth.expected_decision


# ---------------------------------------------------------------------------
# Builder helpers
# ---------------------------------------------------------------------------

def _clean_invoice(seq: int, rng: random.Random) -> SyntheticInvoice:
    """
    Generate a clean invoice that genuinely passes all pipeline checks.

    Vendor assignment: round-robin across the three reference vendors so that
    clean invoices naturally have three distinct base amounts.

    Tax variation: a seeded-random tax between $5.00 and $99.99 (rounded to 2
    decimal places) is added to each invoice.  This makes every total_amount
    unique, preventing POSSIBLE_DUPLICATE cross-firing between clean invoices.

    The line items match the PO exactly (same description, quantity, unit_price)
    so compare_line_items() raises no mismatches.  total_amount = subtotal + tax,
    which stays well under the contract max ($15,000).
    """
    vendor = _VENDORS[seq % len(_VENDORS)]
    inv_num = f"SYNTH-CLEAN-{seq:05d}"

    # Unique tax per invoice — seeded so deterministic; range $5.00–$99.99
    tax = round(rng.uniform(5.0, 99.99), 2)
    total = round(vendor["subtotal"] + tax, 2)

    extracted = {
        "vendor_name": vendor["vendor_name"],
        "vendor_id": vendor["vendor_code"],
        "invoice_number": inv_num,
        "invoice_date": INVOICE_DATE,
        "po_number": vendor["po_number"],
        "contract_number": vendor["contract_number"],
        "currency": "USD",
        "line_items": [
            {
                "description": vendor["product"],
                "quantity": vendor["quantity"],
                "unit_price": vendor["unit_price"],
                "total": vendor["subtotal"],
            }
        ],
        "tax_amount": tax,
        "total_amount": total,
    }
    return SyntheticInvoice(
        invoice_id=inv_num,
        scenario=SCENARIO_CLEAN,
        extracted_data=extracted,
        ground_truth=GroundTruth(
            scenario=SCENARIO_CLEAN,
            expected_decision="STRAIGHT_THROUGH",
            expected_exception_type=None,
            notes=(
                f"Clean invoice — vendor {vendor['vendor_code']}, "
                f"subtotal ${vendor['subtotal']:,.2f}, tax ${tax:.2f}, "
                f"total ${total:,.2f}."
            ),
        ),
    )


def _price_mismatch_invoice(seq: int) -> SyntheticInvoice:
    inv_num = f"SYNTH-PRICE-{seq:05d}"
    bad_price = _VA["unit_price"] * 1.5          # 50% markup
    bad_total_line = round(_VA["quantity"] * bad_price, 2)
    extracted = {
        "vendor_name": _VA["vendor_name"],
        "vendor_id": _VA["vendor_code"],
        "invoice_number": inv_num,
        "invoice_date": INVOICE_DATE,
        "po_number": _VA["po_number"],
        "contract_number": _VA["contract_number"],
        "currency": "USD",
        "line_items": [
            {
                "description": _VA["product"],
                "quantity": _VA["quantity"],
                "unit_price": bad_price,
                "total": bad_total_line,
            }
        ],
        "tax_amount": 0.0,
        "total_amount": bad_total_line,
    }
    return SyntheticInvoice(
        invoice_id=inv_num,
        scenario=SCENARIO_PO_PRICE_MISMATCH,
        extracted_data=extracted,
        ground_truth=GroundTruth(
            scenario=SCENARIO_PO_PRICE_MISMATCH,
            expected_decision="EXCEPTION",
            expected_exception_type="PO_MISMATCH",
            notes=f"Unit price ${bad_price} vs PO ${_VA['unit_price']}.",
        ),
    )


def _quantity_mismatch_invoice(seq: int) -> SyntheticInvoice:
    inv_num = f"SYNTH-QTY-{seq:05d}"
    bad_qty = _VA["quantity"] + 5               # 5 extra units
    bad_total_line = round(bad_qty * _VA["unit_price"], 2)
    extracted = {
        "vendor_name": _VA["vendor_name"],
        "vendor_id": _VA["vendor_code"],
        "invoice_number": inv_num,
        "invoice_date": INVOICE_DATE,
        "po_number": _VA["po_number"],
        "contract_number": _VA["contract_number"],
        "currency": "USD",
        "line_items": [
            {
                "description": _VA["product"],
                "quantity": bad_qty,
                "unit_price": _VA["unit_price"],
                "total": bad_total_line,
            }
        ],
        "tax_amount": 0.0,
        "total_amount": bad_total_line,
    }
    return SyntheticInvoice(
        invoice_id=inv_num,
        scenario=SCENARIO_QUANTITY_MISMATCH,
        extracted_data=extracted,
        ground_truth=GroundTruth(
            scenario=SCENARIO_QUANTITY_MISMATCH,
            expected_decision="EXCEPTION",
            expected_exception_type="PO_MISMATCH",
            notes=f"Quantity {bad_qty} vs PO {_VA['quantity']}.",
        ),
    )


def _unknown_po_invoice(seq: int) -> SyntheticInvoice:
    inv_num = f"SYNTH-UNKNPO-{seq:05d}"
    extracted = {
        "vendor_name": _VA["vendor_name"],
        "vendor_id": _VA["vendor_code"],
        "invoice_number": inv_num,
        "invoice_date": INVOICE_DATE,
        "po_number": "PO-DOES-NOT-EXIST-99999",
        "contract_number": _VA["contract_number"],
        "currency": "USD",
        "line_items": [
            {
                "description": _VA["product"],
                "quantity": _VA["quantity"],
                "unit_price": _VA["unit_price"],
                "total": _VA["subtotal"],
            }
        ],
        "tax_amount": 0.0,
        "total_amount": _VA["subtotal"],
    }
    return SyntheticInvoice(
        invoice_id=inv_num,
        scenario=SCENARIO_UNKNOWN_PO,
        extracted_data=extracted,
        ground_truth=GroundTruth(
            scenario=SCENARIO_UNKNOWN_PO,
            expected_decision="EXCEPTION",
            expected_exception_type="UNKNOWN_PO",
            notes="PO number not in database.",
        ),
    )


def _contract_violation_invoice(seq: int) -> SyntheticInvoice:
    inv_num = f"SYNTH-CTRV-{seq:05d}"
    # Total well above CONTRACT_MAX ($15,000)
    big_qty = 200.0
    big_total = round(big_qty * _VA["unit_price"], 2)   # $20,000
    extracted = {
        "vendor_name": _VA["vendor_name"],
        "vendor_id": _VA["vendor_code"],
        "invoice_number": inv_num,
        "invoice_date": INVOICE_DATE,
        "po_number": _VA["po_number"],
        "contract_number": _VA["contract_number"],
        "currency": "USD",
        "line_items": [
            {
                "description": _VA["product"],
                "quantity": big_qty,
                "unit_price": _VA["unit_price"],
                "total": big_total,
            }
        ],
        "tax_amount": 0.0,
        "total_amount": big_total,
    }
    return SyntheticInvoice(
        invoice_id=inv_num,
        scenario=SCENARIO_CONTRACT_VIOLATION,
        extracted_data=extracted,
        ground_truth=GroundTruth(
            scenario=SCENARIO_CONTRACT_VIOLATION,
            expected_decision="EXCEPTION",
            expected_exception_type="CONTRACT_VIOLATION",
            notes=f"Total ${big_total} exceeds contract max ${_VA['contract_max']}.",
        ),
    )


def _duplicate_invoice(seq: int, original: SyntheticInvoice) -> SyntheticInvoice:
    """
    Deliberately reuses the original invoice's number and total_amount.
    The duplicate_check_node will detect it as DUPLICATE_INVOICE (exact match
    on invoice_number) once the original has been processed and persisted.

    Using the original's extracted_data ensures the amounts also match, which
    would additionally trigger POSSIBLE_DUPLICATE if the exact check were not
    fired first — but the node short-circuits on the exact match.
    """
    orig_data = original.extracted_data
    extracted = {
        **orig_data,
        # invoice_number is intentionally the same as the original
    }
    return SyntheticInvoice(
        invoice_id=f"SYNTH-DUP-{seq:05d}",
        scenario=SCENARIO_DUPLICATE,
        extracted_data=extracted,
        ground_truth=GroundTruth(
            scenario=SCENARIO_DUPLICATE,
            expected_decision="EXCEPTION",
            expected_exception_type="DUPLICATE_INVOICE",
            notes=f"Duplicate of {orig_data.get('invoice_number')}.",
        ),
    )


def _extraction_failure_invoice(seq: int) -> SyntheticInvoice:
    """Empty extracted_data triggers EXTRACTION_FAILED in validation_node."""
    inv_num = f"SYNTH-FAIL-{seq:05d}"
    return SyntheticInvoice(
        invoice_id=inv_num,
        scenario=SCENARIO_EXTRACTION_FAILURE,
        extracted_data={},
        ground_truth=GroundTruth(
            scenario=SCENARIO_EXTRACTION_FAILURE,
            expected_decision="EXCEPTION",
            expected_exception_type="EXTRACTION_FAILED",
            notes="Empty extracted_data simulates unreadable document.",
        ),
    )


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

class SyntheticInvoiceGenerator:
    """
    Generates a list of SyntheticInvoice objects according to a scenario
    distribution.  Pass seed= for reproducible output.

    Usage
    -----
    gen = SyntheticInvoiceGenerator(seed=42)
    invoices = gen.generate(distribution={
        SCENARIO_CLEAN: 5,
        SCENARIO_PO_PRICE_MISMATCH: 2,
    })
    """

    def __init__(self, seed: Optional[int] = None) -> None:
        self._rng = random.Random(seed)

    def generate(
        self,
        distribution: Optional[Dict[str, int]] = None,
    ) -> List[SyntheticInvoice]:
        """
        Return a shuffled list of SyntheticInvoice objects.

        Parameters
        ----------
        distribution:
            Dict mapping scenario name → count.  Defaults to DEFAULT_DISTRIBUTION
            (100 invoices).  Keys must be from ALL_SCENARIOS.

        Raises
        ------
        ValueError if an unrecognised scenario key is supplied.
        """
        if distribution is None:
            distribution = DEFAULT_DISTRIBUTION

        unknown = set(distribution) - set(ALL_SCENARIOS)
        if unknown:
            raise ValueError(f"Unknown scenario(s): {unknown}")

        invoices: List[SyntheticInvoice] = []
        counters = {s: 0 for s in ALL_SCENARIOS}

        clean_count = distribution.get(SCENARIO_CLEAN, 0)
        dup_count = distribution.get(SCENARIO_DUPLICATE, 0)

        # --- Build clean invoices first (duplicates reference them) ---
        clean_invoices: List[SyntheticInvoice] = []
        for i in range(clean_count):
            inv = _clean_invoice(counters[SCENARIO_CLEAN], self._rng)
            clean_invoices.append(inv)
            invoices.append(inv)
            counters[SCENARIO_CLEAN] += 1

        # --- Build price mismatch invoices ---
        for _ in range(distribution.get(SCENARIO_PO_PRICE_MISMATCH, 0)):
            invoices.append(_price_mismatch_invoice(counters[SCENARIO_PO_PRICE_MISMATCH]))
            counters[SCENARIO_PO_PRICE_MISMATCH] += 1

        # --- Build quantity mismatch invoices ---
        for _ in range(distribution.get(SCENARIO_QUANTITY_MISMATCH, 0)):
            invoices.append(_quantity_mismatch_invoice(counters[SCENARIO_QUANTITY_MISMATCH]))
            counters[SCENARIO_QUANTITY_MISMATCH] += 1

        # --- Build unknown PO invoices ---
        for _ in range(distribution.get(SCENARIO_UNKNOWN_PO, 0)):
            invoices.append(_unknown_po_invoice(counters[SCENARIO_UNKNOWN_PO]))
            counters[SCENARIO_UNKNOWN_PO] += 1

        # --- Build contract violation invoices ---
        for _ in range(distribution.get(SCENARIO_CONTRACT_VIOLATION, 0)):
            invoices.append(_contract_violation_invoice(counters[SCENARIO_CONTRACT_VIOLATION]))
            counters[SCENARIO_CONTRACT_VIOLATION] += 1

        # --- Build duplicate invoices (always placed at the end so their
        #     originals are already in the DB when they run) ---
        for i in range(dup_count):
            if clean_invoices:
                # Duplicate a different clean invoice for each dup slot
                original = clean_invoices[i % len(clean_invoices)]
            else:
                # Edge case: no clean invoices — create a synthetic original
                original = _clean_invoice(i, self._rng)
            invoices.append(_duplicate_invoice(counters[SCENARIO_DUPLICATE], original))
            counters[SCENARIO_DUPLICATE] += 1

        # --- Build extraction failure invoices ---
        for _ in range(distribution.get(SCENARIO_EXTRACTION_FAILURE, 0)):
            invoices.append(_extraction_failure_invoice(counters[SCENARIO_EXTRACTION_FAILURE]))
            counters[SCENARIO_EXTRACTION_FAILURE] += 1

        # Shuffle non-duplicate invoices for a realistic mixed sequence.
        # Duplicates stay at the end so their originals are always processed first.
        non_dup = [i for i in invoices if i.scenario != SCENARIO_DUPLICATE]
        dup_invoices = [i for i in invoices if i.scenario == SCENARIO_DUPLICATE]

        self._rng.shuffle(non_dup)
        return non_dup + dup_invoices
