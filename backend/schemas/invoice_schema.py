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
        if abs((self.quantity * self.unit_price) - self.total) > 0.01:
            raise ValueError(f"Line item total {self.total} does not match Qty * Price")
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
        calculated_total = sum(item.total for item in self.line_items) + self.tax_amount
        if abs(calculated_total - self.total_amount) > 0.1:
            raise ValueError("Grand total mismatch: Sum of items + tax != Total Amount")
        return self