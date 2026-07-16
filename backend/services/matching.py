from datetime import datetime


class MatchingEngine:
    @staticmethod
    def compare_line_items(invoice_items: list, po_items: list) -> list:
        """
        Compare invoice line items against PO line items.

        Both lists are expected to be dicts with at least:
          - description (str)
          - unit_price  (float)
          - quantity    (float)

        Returns a list of mismatch description strings.
        """
        mismatches = []

        for inv_item in invoice_items:
            # Match by description (LineItem schema has no sku field)
            po_item = next(
                (p for p in po_items if p.get("description") == inv_item.get("description")),
                None,
            )

            if po_item is None:
                mismatches.append(
                    f"Item '{inv_item.get('description')}' not found in PO"
                )
                continue

            # Price variance check
            price_diff = inv_item.get("unit_price", 0) - po_item.get("unit_price", 0)
            if abs(price_diff) > 0.01:
                mismatches.append(
                    f"Price variance on '{inv_item.get('description')}': "
                    f"invoice ${inv_item.get('unit_price'):.2f} vs PO ${po_item.get('unit_price'):.2f} "
                    f"(diff ${price_diff:+.2f})"
                )

            # Quantity variance check
            qty_diff = inv_item.get("quantity", 0) - po_item.get("quantity", 0)
            if abs(qty_diff) > 0.001:
                mismatches.append(
                    f"Quantity variance on '{inv_item.get('description')}': "
                    f"invoice {inv_item.get('quantity')} vs PO {po_item.get('quantity')}"
                )

        return mismatches

    @staticmethod
    def check_contract_compliance(invoice_data: dict, contract) -> str | None:
        """
        Verify invoice against a Contract ORM object.

        Returns an exception description string, or None if compliant.
        contract fields used: end_date (datetime), max_amount (float)
        invoice_data fields used: invoice_date (str/datetime), total_amount (float)
        """
        # Resolve invoice date — may be a string or datetime
        invoice_date = invoice_data.get("invoice_date")
        if isinstance(invoice_date, str):
            try:
                invoice_date = datetime.fromisoformat(invoice_date)
            except ValueError:
                return "Invalid invoice date format — cannot verify contract compliance"

        # Contract expiry check
        if hasattr(contract, "end_date") and contract.end_date:
            if invoice_date and invoice_date > contract.end_date:
                return f"Contract expired on {contract.end_date.date()}"

        # Contract amount limit check
        total = invoice_data.get("total_amount", 0)
        if contract.max_amount and total > contract.max_amount:
            return (
                f"Invoice total ${total:.2f} exceeds contract limit ${contract.max_amount:.2f}"
            )

        return None
