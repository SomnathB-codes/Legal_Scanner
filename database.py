import json
import os
import streamlit as st

try:
    from supabase import create_client, Client
except ImportError:
    raise ImportError("Run: pip install supabase")


# ── Schema definition — must match the SQL table exactly ─────────────────────

# Columns stored as JSONB in Postgres (Python list → JSON)
JSONB_FIELDS = {"hearing_dates", "business_dates", "interim_orders", "hearing_purposes"}

# All allowed column names (anything else is silently dropped)
ALLOWED_COLUMNS = {
    "cnr_number", "case_type", "filing_number", "registration_number",
    "court_name", "court_level", "district", "state",
    "act_name", "section", "number_of_sections",
    "filing_date", "registration_date", "first_hearing_date",
    "next_hearing_date", "decision_date",
    "is_pending", "is_disposed",
    "hearing_dates", "business_dates", "interim_orders", "hearing_purposes",
    "full_text",   # ← TEXT column — guaranteed to be saved
}


# ── Client ────────────────────────────────────────────────────────────────────

def get_client() -> Client:
    """
    Load Supabase credentials from Streamlit secrets or env vars.

    secrets.toml:
        [supabase]
        url = "https://xxxx.supabase.co"
        key = "your-anon-public-key"
    """
    try:
        url = st.secrets["supabase"]["url"]
        key = st.secrets["supabase"]["key"]
    except (KeyError, FileNotFoundError):
        url = os.getenv("SUPABASE_URL", "")
        key = os.getenv("SUPABASE_KEY", "")

    if not url or not key:
        raise ValueError(
            "❌ Supabase credentials missing.\n"
            "Add to .streamlit/secrets.toml:\n\n"
            "[supabase]\n"
            'url = "https://xxxx.supabase.co"\n'
            'key = "your-anon-public-key"\n'
        )
    return create_client(url, key)


# ── Row preparation ───────────────────────────────────────────────────────────

def _prepare_row(case_json: dict) -> dict:
    """
    Build a clean dict ready for Supabase upsert:
      - Only ALLOWED_COLUMNS are kept
      - JSONB fields: list/None → JSON string (Supabase driver accepts both
        native list and JSON string, but explicit is safer)
      - None values are left as None (Postgres NULL)
      - full_text kept as plain string (never truncated)
    """
    row = {}
    for col in ALLOWED_COLUMNS:
        val = case_json.get(col)   # None if key absent

        if col in JSONB_FIELDS:
            if val is None:
                row[col] = None
            elif isinstance(val, list):
                row[col] = val          # supabase-py handles list → JSONB
            else:
                row[col] = val
        else:
            row[col] = val             # TEXT / INT / None as-is

    return row


# ── Public API ────────────────────────────────────────────────────────────────

def save_case(case_json: dict) -> list:
    """
    Upsert a single case (keyed on cnr_number).
    Returns the Supabase response data list.
    Raises on error.
    """
    client = get_client()
    row    = _prepare_row(case_json)

    # Sanity-check: warn if full_text is missing
    if not row.get("full_text"):
        import streamlit as st
        st.warning("⚠️ full_text is empty — did you upload the Interim Order PDFs?")

    response = (
        client.table("court_cases")
        .upsert(row, on_conflict="cnr_number")
        .execute()
    )
    return response.data


def fetch_all_cases() -> list[dict]:
    """Return all rows from court_cases ordered by filing date desc."""
    client = get_client()
    response = (
        client.table("court_cases")
        .select("*")
        .order("filing_date", desc=True)
        .execute()
    )
    return response.data or []


def fetch_case_by_cnr(cnr: str) -> dict | None:
    """Return a single case by CNR number, or None if not found."""
    client = get_client()
    response = (
        client.table("court_cases")
        .select("*")
        .eq("cnr_number", cnr)
        .limit(1)
        .execute()
    )
    data = response.data
    return data[0] if data else None
