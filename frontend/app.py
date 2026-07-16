import streamlit as st
import pandas as pd
import requests
import plotly.express as px
from datetime import datetime

import os

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# In Docker, set API_URL=http://backend:8000 via environment variable.
# Locally it defaults to localhost.
API_URL = os.getenv("API_URL", "http://localhost:8000")

st.set_page_config(
    page_title="AP Invoice Exception Agent",
    layout="wide",
    page_icon="📑",
)

st.markdown("""
    <style>
    .main { background-color: #f8fafc; }
    [data-testid="stMetric"] {
        background-color: #1e2530;
        padding: 15px;
        border-radius: 10px;
        border: 1px solid #2d3748;
    }
    [data-testid="stMetricLabel"] { color: #a0aec0 !important; }
    [data-testid="stMetricValue"] { color: #f7fafc !important; }
    [data-testid="stMetricDelta"] { color: #68d391 !important; }
    .status-approved { color: #10b981; font-weight: bold; }
    .status-review   { color: #f59e0b; font-weight: bold; }
    .status-rejected { color: #ef4444; font-weight: bold; }
    </style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def api_get(path: str) -> dict | list | None:
    """GET request to the backend. Returns parsed JSON or None on error."""
    try:
        resp = requests.get(f"{API_URL}{path}", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        st.error("Cannot reach the backend. Is the FastAPI server running on port 8000?")
        return None
    except requests.exceptions.HTTPError as exc:
        st.error(f"API error: {exc.response.status_code} — {exc.response.text}")
        return None
    except Exception as exc:
        st.error(f"Unexpected error: {exc}")
        return None


def status_badge(status: str) -> str:
    colour = {
        "STRAIGHT_THROUGH": "#10b981",
        "REVIEW_REQUIRED": "#f59e0b",
        "REJECTED": "#ef4444",
        "PENDING": "#6b7280",
        "EXCEPTION": "#f59e0b",
        "EXTRACTION_FAILED": "#ef4444",
    }.get(status, "#6b7280")
    return f'<span style="color:{colour};font-weight:bold">{status}</span>'


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------

def main():
    st.title("📑 AP Invoice & Contract Agent")
    st.subheader("Enterprise Accounts Payable Automation")

    # -----------------------------------------------------------------------
    # SIDEBAR — Upload + Search
    # -----------------------------------------------------------------------
    with st.sidebar:
        st.header("Upload Invoice")
        uploaded_file = st.file_uploader(
            "Drop PDF or image here", type=["pdf", "png", "jpg", "jpeg"]
        )

        if uploaded_file and st.button("🚀 Process Invoice"):
            with st.spinner("Agent reasoning in progress…"):
                try:
                    files = {
                        "file": (
                            uploaded_file.name,
                            uploaded_file.getvalue(),
                            uploaded_file.type,
                        )
                    }
                    resp = requests.post(
                        f"{API_URL}/upload-invoice", files=files, timeout=120
                    )
                    if resp.status_code == 200:
                        result = resp.json()
                        st.session_state["last_result"] = result
                        st.success("Processing complete!")
                    else:
                        detail = resp.json().get("detail", resp.text)
                        st.error(f"Processing failed ({resp.status_code}): {detail}")
                except requests.exceptions.ConnectionError:
                    st.error("Cannot reach the backend. Is the FastAPI server running?")
                except Exception as exc:
                    st.error(f"Unexpected error: {exc}")

        st.divider()

        # Invoice search
        st.header("Lookup Invoice")
        search_id = st.number_input("Invoice ID", min_value=1, step=1, value=1)
        if st.button("🔍 Fetch Invoice"):
            data = api_get(f"/invoice/{int(search_id)}")
            if data:
                st.session_state["searched_invoice"] = data

    # -----------------------------------------------------------------------
    # LAST UPLOAD RESULT (shown immediately after processing)
    # -----------------------------------------------------------------------
    if "last_result" in st.session_state:
        result = st.session_state["last_result"]
        st.divider()
        st.subheader("📋 Latest Processing Result")

        status = result.get("status", "UNKNOWN")
        st.markdown(
            f"**Invoice ID:** `{result.get('invoice_id')}` &nbsp;|&nbsp; "
            f"**Number:** `{result.get('invoice_number')}` &nbsp;|&nbsp; "
            f"**Decision:** {status_badge(status)}",
            unsafe_allow_html=True,
        )

        col_l, col_r = st.columns(2)

        with col_l:
            st.write("#### Extracted Fields")
            fields = result.get("extracted_fields", {})
            if fields:
                display = {
                    k: v
                    for k, v in fields.items()
                    if k != "line_items"
                }
                st.json(display)

                line_items = fields.get("line_items", [])
                if line_items:
                    st.write("**Line Items**")
                    st.dataframe(pd.DataFrame(line_items), use_container_width=True)
            else:
                st.info("No fields extracted.")

        with col_r:
            st.write("#### Exceptions")
            exceptions = result.get("exceptions", [])
            if exceptions:
                for exc in exceptions:
                    st.error(f"**{exc.get('type')}** — {exc.get('description')}")
            else:
                st.success("No exceptions found.")

            st.write("#### Reasoning Chain")
            reasoning = result.get("reasoning", [])
            for i, step in enumerate(reasoning, 1):
                st.markdown(f"`{i}.` {step}")

    # -----------------------------------------------------------------------
    # SEARCHED INVOICE DETAIL
    # -----------------------------------------------------------------------
    if "searched_invoice" in st.session_state:
        inv = st.session_state["searched_invoice"]
        st.divider()
        st.subheader(f"🔍 Invoice Detail — #{inv.get('id')}")

        st.markdown(
            f"**Number:** `{inv.get('invoice_number')}` &nbsp;|&nbsp; "
            f"**Status:** {status_badge(inv.get('status', ''))} &nbsp;|&nbsp; "
            f"**Total:** `{inv.get('currency', 'USD')} {inv.get('total_amount', 0):,.2f}`",
            unsafe_allow_html=True,
        )

        detail_exceptions = inv.get("exceptions", [])
        if detail_exceptions:
            st.write("**Exceptions:**")
            for exc in detail_exceptions:
                st.error(f"**{exc.get('type')}** — {exc.get('description')}")

        if st.button("📜 Load Audit Trail"):
            audit = api_get(f"/invoice/{inv['id']}/audit")
            if audit:
                st.session_state["audit_data"] = audit

    # -----------------------------------------------------------------------
    # MAIN DASHBOARD TABS
    # -----------------------------------------------------------------------
    st.divider()
    tab1, tab2, tab3 = st.tabs(
        ["📊 Dashboard", "📋 Recent Invoices", "🛡️ Audit Trail"]
    )

    # --- Tab 1: Stats ---
    with tab1:
        stats = api_get("/stats")
        if stats:
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Total Invoices", stats.get("total_invoices", 0))
            col2.metric(
                "Straight-Through Rate",
                f"{stats.get('straight_through_pct', 0)}%",
            )
            col3.metric("Pending Review", stats.get("review_required", 0))
            col4.metric(
                "Scheduled Value",
                f"${stats.get('total_scheduled_value', 0):,.2f}",
            )
        else:
            st.info("Stats unavailable — start the backend to see live data.")

    # --- Tab 2: Recent Invoices ---
    with tab2:
        invoices = api_get("/invoices?limit=50")
        if invoices:
            rows = []
            for inv in invoices:
                rows.append({
                    "ID": inv.get("id"),
                    "Invoice Number": inv.get("invoice_number"),
                    "Status": inv.get("status"),
                    "Total": f"${inv.get('total_amount', 0):,.2f}",
                    "Currency": inv.get("currency", "USD"),
                    "Created": inv.get("created_at", "")[:19] if inv.get("created_at") else "",
                })
            if rows:
                df = pd.DataFrame(rows)
                st.dataframe(df, use_container_width=True, hide_index=True)

                # Exception analytics from same data
                status_counts = df["Status"].value_counts().reset_index()
                status_counts.columns = ["Status", "Count"]
                if not status_counts.empty:
                    st.write("#### Status Breakdown")
                    fig = px.pie(
                        status_counts,
                        values="Count",
                        names="Status",
                        hole=0.35,
                        color="Status",
                        color_discrete_map={
                            "STRAIGHT_THROUGH": "#10b981",
                            "REVIEW_REQUIRED": "#f59e0b",
                            "REJECTED": "#ef4444",
                            "PENDING": "#6b7280",
                        },
                    )
                    st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("No invoices found. Upload an invoice to get started.")
        else:
            st.info("No invoices found or backend unavailable.")

    # --- Tab 3: Audit Trail ---
    with tab3:
        if "audit_data" in st.session_state:
            audit = st.session_state["audit_data"]
            st.write(
                f"### Audit Trail — Invoice #{audit.get('invoice_id')} "
                f"(`{audit.get('invoice_number')}`)"
            )
            trail = audit.get("audit_trail", [])
            if trail:
                for entry in trail:
                    ts = entry.get("timestamp", "")[:19] if entry.get("timestamp") else ""
                    agent = entry.get("agent", "unknown")
                    action = entry.get("action", "")
                    details = entry.get("details", {})
                    reasoning_text = (
                        details.get("reasoning", "") if isinstance(details, dict) else ""
                    )
                    with st.expander(f"[{ts}] {agent} → {action}"):
                        if reasoning_text:
                            st.write(reasoning_text)
                        if details:
                            st.json(details)
            else:
                st.info("No audit records found for this invoice.")
        else:
            st.info(
                "Search for an invoice using the sidebar, then click 'Load Audit Trail'."
            )


if __name__ == "__main__":
    main()
