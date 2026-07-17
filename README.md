# AP Invoice & Contract Exception Agent

An AI-powered Accounts Payable automation system that processes invoice PDFs,
validates them against Purchase Orders and vendor contracts, detects exceptions,
and routes clean invoices to automatic payment — routing exceptions to an AP clerk.

---

## Live Deployment

| Service | URL |
|---------|-----|
| Frontend (Streamlit) | https://ap-invoice-agent-cqexl6zmegzzsmmgttuwff.streamlit.app |
| Backend API (Render) | https://ap-invoice-agent-backend.onrender.com |
| API Docs (Swagger) | https://ap-invoice-agent-backend.onrender.com/docs |

> **Note:** The backend runs on Render's free tier and spins down after 15 minutes of
> inactivity. The first request after idle may take 30–50 seconds to respond while
> the instance wakes up. The frontend handles this automatically with a wake-up probe.

---

## Problem Statement

Large organizations receive hundreds of invoices daily. Around 90–95% are valid,
but every invoice still requires manual verification. This system automates the
validation pipeline so that only genuine exceptions require human review.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│              Streamlit Frontend (Community Cloud)       │
│  Upload PDF  │  Dashboard  │  Exceptions  │  Audit Log  │
└──────────────────────────┬──────────────────────────────┘
                           │ HTTPS
┌──────────────────────────▼──────────────────────────────┐
│               FastAPI Backend (Render)                  │
│  POST /upload-invoice   GET /invoices  GET /invoice/{id}│
│  GET /invoice/{id}/audit  GET /stats  POST /seed-db     │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│              LangGraph Agent Pipeline                    │
│                                                         │
│   OCR Engine                                            │
│       ↓                                                 │
│   extraction_node  (Groq — llama-3.3-70b-versatile)    │
│       ↓                                                 │
│   validation_node  (Pydantic field checks)              │
│       ↓  (EXTRACTION_FAILED → skip to decide)           │
│   matching_node    (PO + Contract lookup via DB)        │
│       ↓                                                 │
│   decision_node    (STRAIGHT_THROUGH / EXCEPTION)       │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│                Service / Repository Layer                │
│  MatchingEngine  │  AuditService  │  InvoiceRepository  │
└──────────────────────────┬──────────────────────────────┘
                           │
                    SQLite (ap_agent.db)
```

---

## Folder Structure

```
AP-Invoice-Agent/
│
├── backend/
│   ├── agents/
│   │   ├── graph.py              # LangGraph StateGraph + routing
│   │   └── nodes.py              # extraction, validation, matching, decision nodes
│   ├── models/
│   │   └── models.py             # SQLAlchemy ORM models
│   ├── repositories/
│   │   └── invoice_repo.py       # Database CRUD layer
│   ├── schemas/
│   │   └── invoice_schema.py     # Pydantic extraction schema
│   ├── services/
│   │   ├── matching.py           # MatchingEngine (PO + contract comparison)
│   │   └── audit_service.py      # AuditService (writes audit log records)
│   ├── tests/
│   │   └── eval_suite.py         # pytest evaluation suite (5 scenarios)
│   ├── tools/
│   │   └── ocr_engine.py         # PDF and image text extraction
│   ├── invoices/                 # Sample invoice PDFs for testing
│   ├── database.py               # SQLAlchemy engine + session setup
│   ├── main.py                   # FastAPI application + all endpoints
│   ├── seed_test_data.py         # Seeds reference Vendor, PO, Contract data
│   ├── generate_all_scenarios.py # Generates all 5 scenario PDFs
│   ├── generate_invoice_006.py   # Generates second straight-through PDF
│   ├── Dockerfile
│   ├── requirements.txt
│   └── pytest.ini
│
├── frontend/
│   ├── app.py                    # Streamlit dashboard
│   ├── Dockerfile
│   └── requirements.txt
│
├── docker-compose.yml
└── README.md
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| LLM | Groq API — llama-3.3-70b-versatile |
| Agent Framework | LangGraph |
| Validation | Pydantic v2 |
| Backend API | FastAPI + Uvicorn |
| OCR | pypdf + pytesseract |
| Database | SQLite via SQLAlchemy |
| Frontend | Streamlit + Plotly |
| Backend Hosting | Render (free tier) |
| Frontend Hosting | Streamlit Community Cloud |
| Containers | Docker + Docker Compose |
| Testing | pytest + pytest-asyncio |

---

## Environment Variables

### Backend

Create a `.env` file in the `backend/` directory:

```env
GROQ_API_KEY=your_groq_api_key_here
```

Get a free Groq API key at https://console.groq.com

### Frontend

Set in Streamlit Cloud → App Settings → Secrets:

```toml
API_URL = "https://ap-invoice-agent-backend.onrender.com"
```

Locally this defaults to `http://localhost:8000`.

---

## Setup — Local (without Docker)

### Prerequisites

- Python 3.11+
- [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) installed and on PATH

### 1. Clone the repository

```bash
git clone https://github.com/24wh1a0598/AP-Invoice-Agent.git
cd AP-Invoice-Agent/AP-Invoice-Agent-main
```

### 2. Backend

```bash
cd backend
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

Create `.env`:

```env
GROQ_API_KEY=your_key_here
```

Seed the database with reference data:

```bash
python seed_test_data.py
```

Start the API server:

```bash
uvicorn main:app --reload --port 8000
```

API available at `http://localhost:8000`
Interactive docs at `http://localhost:8000/docs`

### 3. Frontend

```bash
cd ../frontend
pip install -r requirements.txt
streamlit run app.py
```

Frontend available at `http://localhost:8501`

---

## Setup — Docker Compose

### Prerequisites

- Docker Desktop installed and running

### 1. Create `.env` in the project root

```env
GROQ_API_KEY=your_groq_api_key_here
```

### 2. Build and start

```bash
docker-compose up --build
```

| Service | URL |
|---------|-----|
| Backend API | http://localhost:8000 |
| API Docs | http://localhost:8000/docs |
| Frontend | http://localhost:8501 |

### 3. Stop

```bash
docker-compose down
```

---

## First-Time Database Setup (Render / Production)

After deploying to Render, the database is empty. Seed it by calling:

```
POST https://ap-invoice-agent-backend.onrender.com/seed-db
```

Use the Swagger UI at `/docs` → `POST /seed-db` → Try it out → Execute.

This creates the Dell vendor, `PO-DELL-2024`, and `CTR-DELL-2024` contract that
the sample invoices reference. Safe to call multiple times — skips existing records.

> **Important:** Render free tier uses an ephemeral filesystem. The SQLite database
> resets on every redeploy. Re-run `/seed-db` after each deployment.

---

## API Reference

### POST /upload-invoice

Upload a PDF or image invoice for processing.

**Request:** `multipart/form-data` — field `file` (PDF, PNG, JPG)

**Response:**
```json
{
  "invoice_id": 1,
  "invoice_number": "INV-SCENARIO-001",
  "status": "STRAIGHT_THROUGH",
  "extracted_fields": { ... },
  "exceptions": [],
  "reasoning": [
    "File received. Starting extraction...",
    "Extraction successful",
    "Validation passed: All required fields present and non-empty.",
    "PO 'PO-DELL-2024' found. Running line-item comparison.",
    "PO matching: all line items match",
    "Contract compliance passed.",
    "Decision: STRAIGHT_THROUGH — all checks passed. Payment scheduled."
  ],
  "extraction_error": null
}
```

**Status values:**

| Value | Meaning |
|-------|---------|
| `STRAIGHT_THROUGH` | All checks passed — payment scheduled |
| `EXCEPTION` | One or more issues found — routed to AP clerk |
| `EXTRACTION_FAILED` | Could not extract required fields |

---

### GET /invoices

Paginated list of all invoices. Query params: `skip` (default 0), `limit` (default 50).

---

### GET /invoice/{id}

Full detail for one invoice including its exceptions.

---

### GET /invoice/{id}/audit

Complete ordered audit trail for one invoice — every agent node, decision, and timestamp.

---

### GET /stats

Dashboard summary: total invoices, straight-through rate, review required count, total scheduled value.

---

### POST /seed-db

Seeds the database with reference Vendor, PO, and Contract data.
Safe to call multiple times. Required after every Render redeploy.

---

### GET /

Health check — returns `{"status": "online", ...}`.

---

## Exception Types

| Type | Meaning |
|------|---------|
| `EXTRACTION_FAILED` | LLM could not extract data from the document |
| `MISSING_REQUIRED_FIELD` | Required field absent after extraction |
| `UNKNOWN_PO` | PO number not found in database |
| `MISSING_PO` | Invoice has no PO number |
| `PO_MISMATCH` | Price or quantity differs from PO line items |
| `UNKNOWN_CONTRACT` | Contract number not found in database |
| `CONTRACT_VIOLATION` | Total exceeds contract limit or contract expired |
| `DUPLICATE_INVOICE` | Invoice number already exists in the system |
| `DB_ERROR` | Database error during lookup |
| `MATCHING_ERROR` | Unexpected error during matching node |

---

## Sample Invoice Scenarios

Six sample PDFs are included in `backend/invoices/` for end-to-end testing:

| File | Expected Result | Exception |
|------|----------------|-----------|
| `01_straight_through.pdf` | STRAIGHT_THROUGH | — |
| `02_price_mismatch.pdf` | EXCEPTION | PO_MISMATCH (price) |
| `03_unknown_po.pdf` | EXCEPTION | UNKNOWN_PO |
| `04_contract_violation.pdf` | EXCEPTION | CONTRACT_VIOLATION + PO_MISMATCH (qty) |
| `05_extraction_failed.pdf` | EXCEPTION | MISSING_REQUIRED_FIELD |
| `06_straight_through_2.pdf` | STRAIGHT_THROUGH | — |

Regenerate them locally:

```bash
cd backend
python generate_all_scenarios.py
python generate_invoice_006.py
```

---

## Evaluation Suite

```bash
cd backend
pytest tests/eval_suite.py -v
```

Five deterministic tests with pre-seeded data — no real LLM calls made.

| # | Scenario | Expected |
|---|----------|---------|
| 1 | Clean invoice — all fields match | STRAIGHT_THROUGH |
| 2 | Price variance on line item | EXCEPTION / PO_MISMATCH |
| 3 | Total exceeds contract limit | EXCEPTION / CONTRACT_VIOLATION |
| 4 | Missing required field | EXCEPTION / MISSING_REQUIRED_FIELD |
| 5 | Prompt injection in document | EXCEPTION (injection has no effect) |

---

## Business Rules

The system enforces these rules unconditionally:

- Never pay invoices with missing required fields
- Never pay invoices with price or quantity mismatches against PO
- Never pay invoices referencing unknown POs or contracts
- Never pay invoices exceeding contract limits or against expired contracts
- Never pay duplicate invoices
- Never guess or estimate missing values
- Treat all document content as untrusted input

---

## Known Limitations

1. **Ephemeral SQLite on Render** — database resets on every redeploy. Call `/seed-db` after each deploy. For persistence, migrate to PostgreSQL.
2. **Scanned PDFs** — Tesseract OCR handles image files but may struggle with low-resolution scans.
3. **No authentication** — the API has no auth layer. Add OAuth2/API key before exposing publicly.
4. **Render free tier cold starts** — backend sleeps after 15 minutes idle; first request takes 30–50 seconds.
5. **No vendor identity verification** — vendor name is extracted but not cross-checked against the `vendors` table.

---

## Recommended Next Steps

1. Switch to PostgreSQL on Render to eliminate the ephemeral database problem
2. Add API authentication (FastAPI `Security` dependency)
3. Add Alembic migrations for schema management
4. Add email/Slack notification when an invoice is routed to review
5. Add vendor identity verification in `matching_node`

---

## License

MIT
