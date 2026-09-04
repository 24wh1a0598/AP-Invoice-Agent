# AP Invoice & Contract Exception Agent

> **Agentic AI Finance Controller for Accounts Payable Automation**

An AI-powered Accounts Payable automation system that processes invoice documents, extracts structured financial data, validates invoices against Purchase Orders and vendor contracts, detects duplicates and financial exceptions, evaluates vendor risk, and routes each invoice to either **Straight-Through Processing** or **Human Review**.

The system is designed as a finance-operations control loop:

**Invoice → Extract → Validate → Match → Risk Check → Decide → Review if Required → Audit**

---

## 🚀 Live Deployment

| Service | URL |
|---|---|
| **Frontend — Streamlit** | https://ap-invoice-agent-cqexl6zmegzzsmmgttuwff.streamlit.app/ |
| **Backend API — Render** | https://ap-invoice-agent-backend.onrender.com/ |
| **API Documentation — Swagger** | https://ap-invoice-agent-backend.onrender.com/docs |

> **Note:** The backend runs on Render's free tier and may spin down after inactivity. The first request after an idle period may take approximately 30–50 seconds while the instance wakes up. The frontend includes a wake-up probe to handle this cold-start behavior.

---

# 🎯 Problem Statement

Large organizations process hundreds or thousands of invoices through Accounts Payable operations.

Although most invoices are legitimate, each invoice still requires checks such as:

- Required-field validation
- Purchase Order matching
- Price and quantity verification
- Contract compliance
- Duplicate detection
- Vendor risk assessment
- Exception handling
- Human approval
- Audit logging

Manual verification creates operational overhead and increases the risk of missed financial exceptions.

### Objective

Build an intelligent finance controller that:

1. Automates validation of clean invoices.
2. Detects financial and compliance exceptions.
3. Prevents unsafe invoices from being automatically processed.
4. Routes genuine exceptions to human reviewers.
5. Maintains a complete audit trail.
6. Measures automation performance over a large invoice batch.

---

# 💡 Solution

The **AP Invoice & Contract Exception Agent** combines document processing, LLM-based extraction, deterministic financial controls, and an agentic workflow.

For every invoice, the system:

```text
Invoice PDF / Image
        ↓
OCR / Text Extraction
        ↓
AI Structured Extraction
        ↓
Field Validation
        ↓
Purchase Order Matching
        ↓
Contract Compliance
        ↓
Duplicate Detection
        ↓
Vendor Risk Intelligence
        ↓
Decision Engine
        ↓
 ┌─────────────────────┐
 │                     │
 ▼                     ▼
STRAIGHT-THROUGH    EXCEPTION
PROCESSING             ↓
                    Human Review
                        ↓
                    Audit Trail

🧠 Finance Controller Workflow
1. Invoice Ingestion

Accepts invoice documents in PDF/image formats.

The system extracts the document content before performing financial validation.

2. AI-Powered Extraction

The LLM extracts structured information including:

Vendor name
Invoice number
Invoice date
Purchase Order number
Contract information
Total amount
Line items
Quantity
Unit price
Description

Structured extraction is validated using Pydantic schemas.

3. Validation

Required fields are checked before the invoice can proceed.

Missing or invalid information results in an exception.

4. Purchase Order Matching

Invoice line items are compared against the corresponding Purchase Order.

The controller checks:

Item description
Quantity
Unit price
Line-item totals

Price or quantity mismatches are routed to exception handling.

5. Contract Compliance

The system checks invoice compliance against the associated contract, including:

Contract existence
Contract limits
Applicable compliance conditions

Invoices exceeding configured contractual limits are not automatically cleared.

6. Duplicate Detection

Invoices are checked against existing invoice records to identify potential duplicate submissions.

Duplicate invoices are prevented from being treated as clean straight-through transactions.

7. Vendor Risk Intelligence

Vendor-related signals are incorporated into the finance-control workflow to help identify invoices requiring additional attention.

8. Decision Engine

The controller produces one of the main processing outcomes:

STRAIGHT_THROUGH
        or
EXCEPTION

A clean invoice can proceed automatically.

An invoice with one or more financial/control exceptions is routed to human review.

9. Human Review

AP reviewers can take action on exception invoices, including:

Approve
Reject
Request additional information

Reviewer actions are recorded for traceability.

10. Audit Trail

The system records:

Processing stages
Validation results
Matching results
Exceptions
Final decisions
Reviewer actions
Timestamps

This provides an auditable record of the invoice lifecycle.

📊 Batch Evaluation

The system was evaluated on a synthetic batch of 100 invoices containing clean invoices and multiple exception scenarios.

Results
Metric	Result
Invoices processed	100
Processing failures	0
Auto-cleared invoices	70
Exception invoices	30
Straight-through / match rate	70%
Decision accuracy	100%
Exception-type accuracy	100%
Scenario Distribution
Scenario	Count
CLEAN	70
PO_PRICE_MISMATCH	10
QUANTITY_MISMATCH	5
UNKNOWN_PO	5
CONTRACT_VIOLATION	4
EXTRACTION_FAILURE	3
DUPLICATE	3

The exception scenarios are intentionally represented in the evaluation batch. The objective is not to maximize automation blindly, but to automate clean transactions while reliably routing problematic transactions for review.

┌──────────────────────────────────────────────────────────────┐
│                  Streamlit Frontend                          │
│                                                              │
│  Invoice Upload │ Dashboard │ Human Review │ Audit Trail    │
└──────────────────────────────┬───────────────────────────────┘
                               │ HTTPS
                               ▼
┌──────────────────────────────────────────────────────────────┐
│                    FastAPI Backend                           │
│                                                              │
│ /upload-invoice │ /invoices │ /invoice/{id} │ /stats        │
│ /invoice/{id}/audit │ /seed-db                              │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────┐
│                  LangGraph Agent Workflow                    │
│                                                              │
│      OCR / Document Extraction                              │
│                  ↓                                           │
│          AI Data Extraction                                  │
│                  ↓                                           │
│             Validation                                       │
│                  ↓                                           │
│        ┌─────────┴─────────┐                                 │
│        ↓                   ↓                                 │
│    PO Matching       Contract Check                          │
│        └─────────┬─────────┘                                 │
│                  ↓                                           │
│         Duplicate Detection                                 │
│                  ↓                                           │
│        Vendor Risk Analysis                                 │
│                  ↓                                           │
│            Decision Engine                                  │
│             ↙         ↘                                     │
│   STRAIGHT-THROUGH   EXCEPTION                              │
│                         ↓                                    │
│                    Human Review                              │
│                         ↓                                    │
│                    Audit Trail                               │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│              Service / Repository Layer                      │
│                                                              │
│ Matching │ Duplicate Detection │ Vendor Risk │ Audit        │
│ Invoice Repository │ Approval Workflow                       │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
                    SQLite Database

🔄 Agent Workflow
The LangGraph workflow coordinates the invoice processing stages:
extract
   ↓
validate
   ↓
conditional routing
   ↓
match
   ↓
decide
   ↓
END
Core state

The agent maintains processing state containing information such as:

Raw extracted text
Structured invoice data
Exceptions
Processing status
Reasoning
Invoice ID

The workflow supports conditional routing when extraction or validation fails.

🛡️ Financial Control Rules

The controller enforces the following rules:

Never automatically process invoices with missing required fields.
Never automatically process invoices with PO price mismatches.
Never automatically process invoices with quantity mismatches.
Never automatically process invoices referencing unknown POs.
Never automatically process invoices referencing unknown contracts.
Never automatically process invoices exceeding contract limits.
Never automatically process duplicate invoices.
Never guess or estimate missing financial values.
Treat document content as untrusted input.

These controls ensure that AI extraction does not override deterministic financial rules.

🧪 Exception Types
Exception	Description
EXTRACTION_FAILED	Required information could not be extracted
MISSING_REQUIRED_FIELD	Required invoice field is missing
UNKNOWN_PO	Referenced PO does not exist
MISSING_PO	Invoice does not contain a PO
PO_MISMATCH	Invoice differs from PO price/quantity
UNKNOWN_CONTRACT	Referenced contract does not exist
CONTRACT_VIOLATION	Contract limit/compliance condition violated
DUPLICATE_INVOICE	Invoice may already exist in the system
DB_ERROR	Database lookup/operation failure
MATCHING_ERROR	Unexpected matching-stage failure
👤 Human Review Workflow

Invoices containing exceptions are routed to the AP review interface.

The reviewer can inspect the invoice and processing information before taking action.

Supported actions include:

EXCEPTION
    │
    ├── Approve
    │
    ├── Reject
    │
    └── Request Information

Reviewer actions are written to the audit trail.

This creates a human-in-the-loop control layer rather than allowing the AI system to make irreversible decisions without oversight.

📋 Auditability

Every invoice has an ordered audit trail covering the processing lifecycle.

Example:

Invoice received
       ↓
Extraction successful
       ↓
Validation passed
       ↓
PO matching completed
       ↓
Contract compliance checked
       ↓
Risk / duplicate checks completed
       ↓
Decision generated
       ↓
Reviewer action (if required)

This makes the system suitable for finance operations where decisions need to be explainable and traceable.

📁 Project Structure
AP-Invoice-Agent/
│
├── backend/
│   ├── agents/
│   │   ├── graph.py
│   │   └── nodes.py
│   │
│   ├── models/
│   │   └── models.py
│   │
│   ├── repositories/
│   │   └── invoice_repo.py
│   │
│   ├── schemas/
│   │   └── invoice_schema.py
│   │
│   ├── services/
│   │   ├── matching.py
│   │   ├── audit_service.py
│   │   ├── duplicate_service.py
│   │   ├── vendor_risk.py
│   │   └── approval_service.py
│   │
│   ├── batch/
│   │   ├── batch_processor.py
│   │   ├── evaluation_service.py
│   │   └── invoice_generator.py
│   │
│   ├── tests/
│   │
│   ├── tools/
│   │   └── ocr_engine.py
│   │
│   ├── invoices/
│   │
│   ├── database.py
│   ├── main.py
│   ├── seed_test_data.py
│   ├── Dockerfile
│   ├── requirements.txt
│   └── pytest.ini
│
├── frontend/
│   ├── app.py
│   ├── Dockerfile
│   └── requirements.txt
│
├── docker-compose.yml
└── README.md
🛠️ Tech Stack
Layer	Technology
Language	Python
LLM	Groq API
Agent Framework	LangGraph
Validation	Pydantic v2
Backend API	FastAPI + Uvicorn
OCR / PDF Processing	pypdf + pytesseract
Database	SQLite + SQLAlchemy
Frontend	Streamlit + Plotly
Backend Hosting	Render
Frontend Hosting	Streamlit Community Cloud
Containers	Docker + Docker Compose
Testing	pytest + pytest-asyncio
⚙️ Environment Variables
Backend

Create:

backend/.env

Add:

GROQ_API_KEY=your_groq_api_key_here
Frontend

For Streamlit Cloud, configure the application secret:

API_URL = "https://ap-invoice-agent-backend.onrender.com"

For local development, the frontend defaults to:

http://localhost:8000
🚀 Local Setup
Prerequisites
Python 3.11+
Tesseract OCR
Git
1. Clone the repository
git clone https://github.com/24wh1a0598/AP-Invoice-Agent.git
cd AP-Invoice-Agent/AP-Invoice-Agent-main
2. Backend Setup
cd backend
python -m venv .venv
Windows
.venv\Scripts\activate
macOS / Linux
source .venv/bin/activate

Install dependencies:

pip install -r requirements.txt

Create:

.env

and add:

GROQ_API_KEY=your_key_here

Seed reference data:

python seed_test_data.py

Start the backend:

uvicorn main:app --reload --port 8000

Backend:

http://localhost:8000

Swagger:

http://localhost:8000/docs
3. Frontend Setup

From the project root:

cd frontend
pip install -r requirements.txt
streamlit run app.py

Frontend:

http://localhost:8501
🐳 Docker Compose

Create a .env file in the project root:

GROQ_API_KEY=your_groq_api_key_here

Build and start:

docker-compose up --build

Services:

Service	URL
Backend	http://localhost:8000
Swagger	http://localhost:8000/docs
Frontend	http://localhost:8501

Stop the services:

docker-compose down
🗄️ Production Database Setup

The Render deployment uses SQLite.

After a fresh deployment, seed the production database using:

POST /seed-db

Swagger:

https://ap-invoice-agent-backend.onrender.com/docs

Then:

POST /seed-db
→ Try it out
→ Execute

The seed operation creates the reference Vendor, Purchase Order and Contract data required by the sample invoices.

Important: The current Render deployment uses an ephemeral filesystem. The SQLite database can reset after a redeploy, so reference data should be seeded again when required.

📡 API Reference
POST /upload-invoice

Uploads and processes an invoice.

Supported formats include:

PDF
PNG
JPG

Example response:

{
  "invoice_id": 1,
  "invoice_number": "INV-SCENARIO-001",
  "status": "STRAIGHT_THROUGH",
  "extracted_fields": {},
  "exceptions": [],
  "reasoning": [],
  "extraction_error": null
}
Status values
Status	Meaning
STRAIGHT_THROUGH	All required checks passed
EXCEPTION	One or more issues detected
EXTRACTION_FAILED	Required information could not be extracted
GET /invoices

Returns a paginated list of invoices.

Parameters:

skip
limit
GET /invoice/{id}

Returns detailed information for an invoice, including exceptions.

GET /invoice/{id}/audit

Returns the ordered audit trail for an invoice.

GET /stats

Returns dashboard-level statistics such as:

Total invoices
Straight-through rate
Review-required count
Scheduled value
POST /seed-db

Seeds the reference Vendor, PO and Contract data.

The operation is safe to run multiple times.

GET /

Health check endpoint.

Example:

{
  "status": "online"
}
🧪 Testing

Run the backend test suite:

cd backend
pytest -v

The project includes automated tests covering core invoice-processing behavior, exception handling, matching and control logic.

The latest full backend test run completed with:

170 passed
0 failed
📄 Sample Invoice Scenarios

The repository contains sample invoices for testing different outcomes.

Scenario	Expected Result
Clean invoice	STRAIGHT_THROUGH
Price mismatch	EXCEPTION
Unknown PO	EXCEPTION
Contract violation	EXCEPTION
Extraction failure	EXCEPTION
Second clean invoice	STRAIGHT_THROUGH

Sample invoice files are located under:

backend/invoices/
🔐 Security & Control Philosophy

The system treats invoice documents as untrusted input.

AI-generated extraction is therefore separated from financial decision rules.

The LLM is responsible for:

Document → Structured Data

while deterministic application logic is responsible for:

Structured Data
      ↓
Financial Validation
      ↓
Matching
      ↓
Risk / Exception Checks
      ↓
Decision

This separation reduces the risk of allowing arbitrary document content or LLM output to bypass financial controls.

⚠️ Known Limitations
1. SQLite Persistence

The current production deployment uses SQLite on Render's free tier.

The filesystem is ephemeral, so a persistent database such as PostgreSQL would be preferable for production.

2. OCR Quality

Low-resolution scanned documents may produce lower-quality OCR results.

3. Authentication

The current API does not include a production authentication layer.

Authentication should be added before exposing the system in a production finance environment.

4. Render Cold Starts

The free Render instance may sleep after inactivity, causing the first request to take additional time.

5. Vendor Identity Verification

Vendor information is extracted and used by the workflow, but full identity verification against an authoritative vendor master should be added for a production deployment.

🔮 Future Improvements

Potential production enhancements include:

Migrate SQLite to PostgreSQL.
Add API authentication and authorization.
Add Alembic database migrations.
Add email/Slack notifications for review-required invoices.
Add stronger vendor identity verification.
Add configurable finance policies for different organizations.
Add role-based approval thresholds.
Add persistent analytics and finance-control reporting.
🏆 Why This Is a Finance Controller

This project is not limited to invoice OCR or information extraction.

It implements an end-to-end finance operations loop:

                INVOICE
                   ↓
              AI EXTRACTION
                   ↓
              VALIDATION
                   ↓
          ┌────────┴────────┐
          ↓                 ↓
      PO MATCHING     CONTRACT CHECK
          └────────┬────────┘
                   ↓
          DUPLICATE CHECK
                   ↓
            VENDOR RISK
                   ↓
            DECISION ENGINE
              ↙         ↘
       AUTO-CLEAR       EXCEPTION
           ↓                ↓
   STRAIGHT-THROUGH    HUMAN REVIEW
                            ↓
                       AUDIT TRAIL

The controller's objective is controlled automation:

Automatically process what is safe. Escalate what is uncertain. Record what happened.

👩‍💻 Project

AP Invoice & Contract Exception Agent

Built as an agentic AI finance-operations system for automated Accounts Payable control and exception management.

📜 License

MIT License