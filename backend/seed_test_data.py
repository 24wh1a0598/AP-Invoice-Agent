from database import SessionLocal, engine, Base
from models.models import Vendor, PurchaseOrder, Contract

# Create tables if they don't already exist
Base.metadata.create_all(bind=engine)

db = SessionLocal()

# Avoid inserting duplicates if you run the script multiple times
existing_vendor = db.query(Vendor).filter_by(vendor_code="ACME").first()

if not existing_vendor:
    vendor = Vendor(
        name="Acme Supplies",
        vendor_code="ACME"
    )
    db.add(vendor)
    db.commit()
    db.refresh(vendor)
else:
    vendor = existing_vendor

# Purchase Order
existing_po = db.query(PurchaseOrder).filter_by(po_number="PO-5551").first()

if not existing_po:
    po = PurchaseOrder(
        po_number="PO-5551",
        vendor_id=vendor.id,
        total_amount=12480.0,
        status="OPEN",
        line_items=[
            {
                "description": "Laptop",
                "quantity": 120,
                "unit_price": 42.0,
                "total": 5040.0
            },
            {
                "description": "Monitor",
                "quantity": 20,
                "unit_price": 372.0,
                "total": 7440.0
            }
        ]
    )
    db.add(po)

# Contract
existing_contract = db.query(Contract).filter_by(contract_number="CON-1001").first()

if not existing_contract:
    contract = Contract(
        contract_number="CON-1001",
        vendor_id=vendor.id,
        max_amount=15000.0
    )
    db.add(contract)

db.commit()
db.close()

print("✅ Test data seeded successfully!")