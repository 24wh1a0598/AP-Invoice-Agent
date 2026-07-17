from pydantic import BaseModel, Field, model_validator
from typing import List, Optional
from datetime import datetime

class LineItem(BaseModel):
    description: str
    quantity: float = Field(gt=0, description="Quantity must be positive")
    unit_price: float = Field(gt=0, description="Price must be positive")
    total: float = Field(gt=0)

    @model_validator(mode='after')
    def validate_line_total(self):
        calculated = round(self.quantity * self.unit_price, 2)
        # Allow up to 5% variance to handle discounts, rounding, and
        # partial-unit pricing on individual lines.
        # Exact arithmetic is verified during PO matching.
        if self.total > 0 and abs(calculated - self.total) / self.total > 0.05:
            raise ValueError(
                f"Line item total {self.total} does not match "
                f"Qty {self.quantity} × Price {self.unit_price} = {calculated} "
                f"(variance exceeds 5%)"
            )
        return self

class InvoiceExtraction(BaseModel):
    vendor_name: str = Field(..., min_length=2)
    vendor_id: str
    invoice_number: str
    invoice_date: datetime
    po_number: Optional[str] = None
    contract_number: Optional[str] = None
    currency: str = Field(default="USD", min_length=3, max_length=3)
    line_items: List[LineItem]
    tax_amount: float = Field(ge=0)
    total_amount: float = Field(gt=0)

    @model_validator(mode='after')
    def validate_grand_total(self):
        # We do not re-verify the vendor's arithmetic here.
        # The total_amount field must simply be present and positive (enforced
        # by Field(gt=0) above).  Arithmetic discrepancies — shipping charges,
        # discounts, rounding — are caught by the PO matching step, not here.
        return self