"""
Generates a second clean straight-through invoice: INV-SCENARIO-006
Same vendor, PO, contract, and line items as scenario 1 — just a new invoice number and date.

Run from backend/:
    python generate_invoice_006.py
"""

import os
os.makedirs("invoices", exist_ok=True)

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

doc = SimpleDocTemplate(
    "invoices/06_straight_through_2.pdf", pagesize=A4,
    rightMargin=20*mm, leftMargin=20*mm,
    topMargin=20*mm, bottomMargin=20*mm,
)
styles = getSampleStyleSheet()
bold   = ParagraphStyle("bold", fontSize=11, fontName="Helvetica-Bold", spaceAfter=2)
normal = styles["Normal"]
sub    = ParagraphStyle("sub",  fontSize=10, textColor=colors.grey, spaceAfter=2)
elements = []

# Header
elements.append(Paragraph("TAX INVOICE", ParagraphStyle("h", fontSize=22, spaceAfter=2, leading=26)))
elements.append(Paragraph("Dell Technologies Inc.", bold))
elements.append(Paragraph("One Dell Way, Round Rock, TX 78682, USA", sub))
elements.append(Spacer(1, 6*mm))

# Meta
meta = [
    ["Invoice Number:", "INV-SCENARIO-006"],
    ["Invoice Date:",   "2024-07-01"],
    ["Due Date:",       "2024-07-31"],
    ["PO Number:",      "PO-DELL-2024"],
    ["Contract Number:", "CTR-DELL-2024"],
    ["Currency:",       "USD"],
]
mt = Table(meta, colWidths=[50*mm, 100*mm])
mt.setStyle(TableStyle([
    ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
    ("FONTSIZE", (0, 0), (-1, -1), 10),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
]))
elements.append(mt)
elements.append(Spacer(1, 6*mm))

# Bill To
elements.append(Paragraph("Bill To:", bold))
elements.append(Paragraph("Acme Corporation, 123 Business Park, New York, NY 10001", normal))
elements.append(Paragraph("Vendor ID: DELL-001", normal))
elements.append(Spacer(1, 5*mm))

# Line Items
elements.append(Paragraph("Line Items", bold))
elements.append(Spacer(1, 2*mm))

rows = [
    ["#", "Description", "Qty", "Unit Price (USD)", "Total (USD)"],
    ["1", "Dell Latitude 5540 Laptop",         "10", "850.00",  "8500.00"],
    ["2", 'Dell 27" Monitor P2723D',            "5",  "320.00",  "1600.00"],
    ["3", "Dell Wireless Keyboard and Mouse",  "15",  "45.00",   "675.00"],
]
t = Table(rows, colWidths=[10*mm, 75*mm, 20*mm, 35*mm, 30*mm])
t.setStyle(TableStyle([
    ("BACKGROUND",    (0, 0), (-1, 0),  colors.HexColor("#1a1a2e")),
    ("TEXTCOLOR",     (0, 0), (-1, 0),  colors.white),
    ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
    ("FONTSIZE",      (0, 0), (-1, -1), 10),
    ("ALIGN",         (2, 0), (-1, -1), "RIGHT"),
    ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
    ("GRID",          (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ("TOPPADDING",    (0, 0), (-1, -1), 6),
]))
elements.append(t)
elements.append(Spacer(1, 5*mm))

# Totals
totals = [
    ["", "Subtotal:",  "$10,775.00"],
    ["", "Tax (0%):",  "$0.00"],
    ["", "TOTAL DUE:", "$10,775.00"],
]
tt = Table(totals, colWidths=[95*mm, 40*mm, 35*mm])
tt.setStyle(TableStyle([
    ("FONTNAME",      (1, -1), (-1, -1), "Helvetica-Bold"),
    ("FONTSIZE",      (0, 0),  (-1, -1), 10),
    ("ALIGN",         (1, 0),  (-1, -1), "RIGHT"),
    ("LINEABOVE",     (1, -1), (-1, -1), 1, colors.black),
    ("BOTTOMPADDING", (0, 0),  (-1, -1), 4),
]))
elements.append(tt)
elements.append(Spacer(1, 8*mm))

# Payment Terms
elements.append(Paragraph("Payment Terms", bold))
elements.append(Paragraph("Net 30 days. Bank transfer to Dell Technologies Inc.", normal))
elements.append(Paragraph("Bank: Chase Bank  |  Account: 1234567890  |  Routing: 021000021", normal))
elements.append(Spacer(1, 5*mm))

# Contract Reference — written out explicitly for LLM extraction
elements.append(Paragraph("Contract Reference", bold))
elements.append(Paragraph(
    "This invoice is issued under Vendor Contract Number: CTR-DELL-2024, "
    "effective 2024-01-01 to 2025-12-31, maximum approved value USD 50,000.00.",
    normal,
))
elements.append(Spacer(1, 5*mm))

# Footer
elements.append(Paragraph(
    "This is a computer-generated invoice. All amounts in USD.",
    ParagraphStyle("foot", fontSize=8, textColor=colors.grey),
))

doc.build(elements)
print("Created: invoices/06_straight_through_2.pdf")
print("Invoice Number : INV-SCENARIO-006")
print("PO Number      : PO-DELL-2024")
print("Contract       : CTR-DELL-2024")
print("Total          : $10,775.00")
print("Expected       : STRAIGHT_THROUGH")
