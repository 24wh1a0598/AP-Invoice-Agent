"""
Run this script from the backend/ directory to generate a sample invoice PDF.
    python generate_sample_invoice.py

Requires: pip install reportlab
"""

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_RIGHT, TA_CENTER


def generate_invoice(output_path="sample_invoice_clean.pdf"):
    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )

    styles = getSampleStyleSheet()
    elements = []

    # --- Header ---
    header_style = ParagraphStyle("header", fontSize=22, spaceAfter=2, leading=26)
    sub_style = ParagraphStyle("sub", fontSize=10, textColor=colors.grey, spaceAfter=2)
    right_style = ParagraphStyle("right", fontSize=10, alignment=TA_RIGHT)
    bold_style = ParagraphStyle("bold", fontSize=11, fontName="Helvetica-Bold")
    normal = styles["Normal"]

    elements.append(Paragraph("TAX INVOICE", header_style))
    elements.append(Paragraph("Dell Technologies Inc.", bold_style))
    elements.append(Paragraph("One Dell Way, Round Rock, TX 78682, USA", sub_style))
    elements.append(Paragraph("Tax ID: US-98-7654321", sub_style))
    elements.append(Spacer(1, 8 * mm))

    # --- Invoice Meta ---
    meta_data = [
        ["Invoice Number:", "INV-2024-DELL-002"],
        ["Invoice Date:", "2024-06-15"],
        ["Due Date:", "2024-07-15"],
        ["PO Number:", "PO-DELL-2024"],
        ["Contract Number:", "CTR-DELL-2024"],
        ["Currency:", "USD"],
    ]
    meta_table = Table(meta_data, colWidths=[50 * mm, 80 * mm])
    meta_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(meta_table)
    elements.append(Spacer(1, 8 * mm))

    # --- Bill To ---
    elements.append(Paragraph("Bill To:", bold_style))
    elements.append(Paragraph("Acme Corporation", normal))
    elements.append(Paragraph("123 Business Park, New York, NY 10001", normal))
    elements.append(Paragraph("Vendor ID: DELL-001", normal))
    elements.append(Spacer(1, 8 * mm))

    # --- Line Items ---
    elements.append(Paragraph("Line Items", bold_style))
    elements.append(Spacer(1, 3 * mm))

    line_items = [
        ["#", "Description", "Quantity", "Unit Price (USD)", "Total (USD)"],
        ["1", "Dell Latitude 5540 Laptop", "10", "850.00", "8,500.00"],
        ["2", "Dell 27\" Monitor P2723D", "5",  "320.00", "1,600.00"],
        ["3", "Dell Wireless Keyboard & Mouse", "15", "45.00",  "675.00"],
    ]

    item_table = Table(
        line_items,
        colWidths=[10 * mm, 75 * mm, 25 * mm, 35 * mm, 30 * mm],
    )
    item_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(item_table)
    elements.append(Spacer(1, 6 * mm))

    # --- Totals ---
    subtotal = 8500.00 + 1600.00 + 675.00   # 10775.00
    tax = 0.00
    total = subtotal + tax                   # 10775.00

    totals_data = [
        ["", "Subtotal:", f"${subtotal:,.2f}"],
        ["", "Tax (0%):", f"${tax:,.2f}"],
        ["", "TOTAL DUE:", f"${total:,.2f}"],
    ]
    totals_table = Table(totals_data, colWidths=[95 * mm, 40 * mm, 35 * mm])
    totals_table.setStyle(TableStyle([
        ("FONTNAME", (1, -1), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("LINEABOVE", (1, -1), (-1, -1), 1, colors.black),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(totals_table)
    elements.append(Spacer(1, 10 * mm))

    # --- Payment Terms ---
    elements.append(Paragraph("Payment Terms", bold_style))
    elements.append(Paragraph("Net 30 days. Bank transfer to Dell Technologies Inc.", normal))
    elements.append(Paragraph("Bank: Chase Bank  |  Account: 1234567890  |  Routing: 021000021", normal))
    elements.append(Spacer(1, 6 * mm))

    # --- Contract Reference (explicit for AI extraction) ---
    elements.append(Paragraph("Contract Reference", bold_style))
    elements.append(Paragraph(
        "This invoice is issued under Vendor Contract Number: CTR-DELL-2024, "
        "effective 2024-01-01 to 2025-12-31, maximum approved value USD 50,000.00.",
        normal,
    ))
    elements.append(Spacer(1, 6 * mm))

    # --- Footer note ---
    note_style = ParagraphStyle("note", fontSize=8, textColor=colors.grey)
    elements.append(Paragraph(
        "This is a computer-generated invoice. All amounts in USD. "
        "Please quote the invoice number in all correspondence.",
        note_style,
    ))

    doc.build(elements)
    print(f"DONE: Invoice generated: {output_path}")
    print(f"   Invoice Number : INV-2024-DELL-002")
    print(f"   PO Number      : PO-DELL-2024")
    print(f"   Contract       : CTR-DELL-2024")
    print(f"   Total          : $10,775.00")
    print()
    print("To seed matching PO and Contract into the DB, run:")
    print("   python seed_test_data.py")


if __name__ == "__main__":
    generate_invoice()
