"""
Generates 5 sample invoice PDFs — one for each possible final status.

Run from the backend/ directory:
    python generate_all_scenarios.py

Also seeds the database with the required reference data.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle


def _build_pdf(output_path, meta_rows, line_items, subtotal, tax, total, notes=None):
    """Shared PDF builder used by all scenario generators."""
    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        rightMargin=20*mm, leftMargin=20*mm,
        topMargin=20*mm, bottomMargin=20*mm,
    )
    styles = getSampleStyleSheet()
    bold = ParagraphStyle("bold", fontSize=11, fontName="Helvetica-Bold", spaceAfter=2)
    normal = styles["Normal"]
    sub = ParagraphStyle("sub", fontSize=10, textColor=colors.grey, spaceAfter=2)
    note_style = ParagraphStyle("note", fontSize=9, textColor=colors.HexColor("#b45309"),
                                backColor=colors.HexColor("#fef3c7"), leading=14)
    elements = []

    # Header
    elements.append(Paragraph("TAX INVOICE", ParagraphStyle("h", fontSize=22, spaceAfter=2, leading=26)))
    elements.append(Paragraph("Dell Technologies Inc.", bold))
    elements.append(Paragraph("One Dell Way, Round Rock, TX 78682, USA", sub))
    elements.append(Spacer(1, 6*mm))

    # Meta
    meta_table = Table(meta_rows, colWidths=[50*mm, 100*mm])
    meta_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(meta_table)
    elements.append(Spacer(1, 6*mm))

    # Bill To
    elements.append(Paragraph("Bill To:", bold))
    elements.append(Paragraph("Acme Corporation, 123 Business Park, New York, NY 10001", normal))
    elements.append(Spacer(1, 5*mm))

    # Optional scenario note box
    if notes:
        elements.append(Paragraph(f"NOTE: {notes}", note_style))
        elements.append(Spacer(1, 4*mm))

    # Line items table
    elements.append(Paragraph("Line Items", bold))
    elements.append(Spacer(1, 2*mm))
    header = [["#", "Description", "Qty", "Unit Price (USD)", "Total (USD)"]]
    table_data = header + [
        [str(i+1), row[0], str(row[1]), f"{row[2]:.2f}", f"{row[3]:.2f}"]
        for i, row in enumerate(line_items)
    ]
    t = Table(table_data, colWidths=[10*mm, 75*mm, 20*mm, 35*mm, 30*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1a1a2e")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,-1), 10),
        ("ALIGN", (2,0), (-1,-1), "RIGHT"),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#f5f5f5")]),
        ("GRID", (0,0), (-1,-1), 0.5, colors.HexColor("#cccccc")),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ("TOPPADDING", (0,0), (-1,-1), 6),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 5*mm))

    # Totals
    totals = [
        ["", "Subtotal:", f"${subtotal:,.2f}"],
        ["", "Tax:",      f"${tax:,.2f}"],
        ["", "TOTAL DUE:", f"${total:,.2f}"],
    ]
    tt = Table(totals, colWidths=[95*mm, 40*mm, 35*mm])
    tt.setStyle(TableStyle([
        ("FONTNAME", (1,-1), (-1,-1), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,-1), 10),
        ("ALIGN", (1,0), (-1,-1), "RIGHT"),
        ("LINEABOVE", (1,-1), (-1,-1), 1, colors.black),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
    ]))
    elements.append(tt)
    elements.append(Spacer(1, 8*mm))

    # Footer
    elements.append(Paragraph(
        "This is a computer-generated invoice. All amounts in USD.",
        ParagraphStyle("foot", fontSize=8, textColor=colors.grey)
    ))

    doc.build(elements)
    print(f"  Created: {output_path}")


# ---------------------------------------------------------------------------
# Scenario 1 — STRAIGHT_THROUGH
# All fields present, PO matches, contract within limit.
# ---------------------------------------------------------------------------
def scenario_1_straight_through():
    meta = [
        ["Invoice Number:", "INV-SCENARIO-001"],
        ["Invoice Date:",   "2024-06-15"],
        ["PO Number:",      "PO-DELL-2024"],
        ["Contract Number:", "CTR-DELL-2024"],
        ["Currency:",       "USD"],
    ]
    items = [
        ("Dell Latitude 5540 Laptop",       10, 850.00, 8500.00),
        ('Dell 27" Monitor P2723D',          5, 320.00, 1600.00),
        ("Dell Wireless Keyboard & Mouse",  15,  45.00,  675.00),
    ]
    _build_pdf(
        "invoices/01_straight_through.pdf",
        meta, items,
        subtotal=10775.00, tax=0.00, total=10775.00,
        notes=None
    )


# ---------------------------------------------------------------------------
# Scenario 2 — EXCEPTION: Price Mismatch
# Unit prices differ from what is stored in PO-DELL-2024.
# PO expects Laptop @ $850, invoice charges $1,200.
# ---------------------------------------------------------------------------
def scenario_2_price_mismatch():
    meta = [
        ["Invoice Number:", "INV-SCENARIO-002"],
        ["Invoice Date:",   "2024-06-15"],
        ["PO Number:",      "PO-DELL-2024"],
        ["Contract Number:", "CTR-DELL-2024"],
        ["Currency:",       "USD"],
    ]
    # Laptop price inflated: $850 -> $1,200 (+$3,500 total variance on 10 units)
    items = [
        ("Dell Latitude 5540 Laptop",       10, 1200.00, 12000.00),
        ('Dell 27" Monitor P2723D',          5,  320.00,  1600.00),
        ("Dell Wireless Keyboard & Mouse",  15,   45.00,   675.00),
    ]
    _build_pdf(
        "invoices/02_price_mismatch.pdf",
        meta, items,
        subtotal=14275.00, tax=0.00, total=14275.00,
        notes="Laptop unit price is $1,200.00 — PO expects $850.00. This will trigger a PO_MISMATCH exception."
    )


# ---------------------------------------------------------------------------
# Scenario 3 — EXCEPTION: Unknown PO
# PO number does not exist in the system.
# ---------------------------------------------------------------------------
def scenario_3_unknown_po():
    meta = [
        ["Invoice Number:", "INV-SCENARIO-003"],
        ["Invoice Date:",   "2024-06-15"],
        ["PO Number:",      "PO-UNKNOWN-9999"],
        ["Contract Number:", "CTR-DELL-2024"],
        ["Currency:",       "USD"],
    ]
    items = [
        ("Dell Latitude 5540 Laptop",  10, 850.00, 8500.00),
        ('Dell 27" Monitor P2723D',     5, 320.00, 1600.00),
    ]
    _build_pdf(
        "invoices/03_unknown_po.pdf",
        meta, items,
        subtotal=10100.00, tax=0.00, total=10100.00,
        notes="PO-UNKNOWN-9999 does not exist in the system. This will trigger an UNKNOWN_PO exception."
    )


# ---------------------------------------------------------------------------
# Scenario 4 — EXCEPTION: Contract Limit Exceeded
# Invoice total ($68,000) exceeds contract max_amount ($50,000).
# ---------------------------------------------------------------------------
def scenario_4_contract_violation():
    meta = [
        ["Invoice Number:", "INV-SCENARIO-004"],
        ["Invoice Date:",   "2024-06-15"],
        ["PO Number:",      "PO-DELL-2024"],
        ["Contract Number:", "CTR-DELL-2024"],
        ["Currency:",       "USD"],
    ]
    # 80 laptops @ $850 = $68,000 — exceeds contract max of $50,000
    items = [
        ("Dell Latitude 5540 Laptop",  80, 850.00, 68000.00),
    ]
    _build_pdf(
        "invoices/04_contract_violation.pdf",
        meta, items,
        subtotal=68000.00, tax=0.00, total=68000.00,
        notes="Total $68,000 exceeds the contract maximum of $50,000. This will trigger a CONTRACT_VIOLATION exception."
    )


# ---------------------------------------------------------------------------
# Scenario 5 — EXTRACTION_FAILED: Missing Required Fields
# The invoice deliberately omits the PO Number field.
# ---------------------------------------------------------------------------
def scenario_5_extraction_failed():
    meta = [
        ["Invoice Number:", "INV-SCENARIO-005"],
        ["Invoice Date:",   "2024-06-15"],
        # PO Number intentionally omitted
        ["Currency:",       "USD"],
    ]
    items = [
        ("Dell Latitude 5540 Laptop", 10, 850.00, 8500.00),
    ]
    _build_pdf(
        "invoices/05_extraction_failed.pdf",
        meta, items,
        subtotal=8500.00, tax=0.00, total=8500.00,
        notes="PO Number is intentionally missing from this invoice. This will trigger a MISSING_REQUIRED_FIELD exception."
    )


# ---------------------------------------------------------------------------
# Seed DB with required reference data
# ---------------------------------------------------------------------------
def seed_db():
    from database import SessionLocal, engine, Base
    import models.models
    from models.models import Vendor, PurchaseOrder, Contract
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        vendor = db.query(Vendor).filter(Vendor.vendor_code == "DELL-001").first()
        if not vendor:
            vendor = Vendor(name="Dell Technologies Inc.", vendor_code="DELL-001")
            db.add(vendor)
            db.flush()
            print(f"  Seeded vendor: {vendor.name}")
        else:
            print(f"  Vendor DELL-001 already exists.")

        if not db.query(PurchaseOrder).filter(PurchaseOrder.po_number == "PO-DELL-2024").first():
            po = PurchaseOrder(
                po_number="PO-DELL-2024",
                vendor_id=vendor.id,
                total_amount=10775.00,
                status="OPEN",
                line_items=[
                    {"description": "Dell Latitude 5540 Laptop",      "quantity": 10.0, "unit_price": 850.00, "total": 8500.00},
                    {"description": 'Dell 27" Monitor P2723D',         "quantity": 5.0,  "unit_price": 320.00, "total": 1600.00},
                    {"description": "Dell Wireless Keyboard & Mouse",  "quantity": 15.0, "unit_price": 45.00,  "total": 675.00},
                ],
            )
            db.add(po)
            print("  Seeded PO: PO-DELL-2024")
        else:
            print("  PO PO-DELL-2024 already exists.")

        if not db.query(Contract).filter(Contract.contract_number == "CTR-DELL-2024").first():
            contract = Contract(
                contract_number="CTR-DELL-2024",
                vendor_id=vendor.id,
                max_amount=50000.00,
            )
            db.add(contract)
            print("  Seeded contract: CTR-DELL-2024 (max $50,000)")
        else:
            print("  Contract CTR-DELL-2024 already exists.")

        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import os
    os.makedirs("invoices", exist_ok=True)

    print("Seeding database...")
    seed_db()

    print("\nGenerating invoice PDFs...")
    scenario_1_straight_through()
    scenario_2_price_mismatch()
    scenario_3_unknown_po()
    scenario_4_contract_violation()
    scenario_5_extraction_failed()

    print("\nAll done. Upload these files to see each status:")
    print("  invoices/01_straight_through.pdf    -> STRAIGHT_THROUGH")
    print("  invoices/02_price_mismatch.pdf      -> EXCEPTION (PO_MISMATCH)")
    print("  invoices/03_unknown_po.pdf          -> EXCEPTION (UNKNOWN_PO)")
    print("  invoices/04_contract_violation.pdf  -> EXCEPTION (CONTRACT_VIOLATION)")
    print("  invoices/05_extraction_failed.pdf   -> EXCEPTION (MISSING_REQUIRED_FIELD)")
