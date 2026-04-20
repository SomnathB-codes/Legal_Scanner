import json
import os
import streamlit as st

try:
    import gspread
    from google.oauth2.service_account import Credentials
except ImportError:
    raise ImportError("Run: pip install gspread google-auth")


# ── All columns in exact order written to the sheet ───────────────────────────
# ⚠️  full_text MUST be last — it can be very large
SHEET_COLUMNS = [
    "cnr_number",
    "case_type",
    "filing_number",
    "registration_number",
    "court_name",
    "court_level",
    "district",
    "state",
    "act_name",
    "section",
    "number_of_sections",
    "filing_date",
    "registration_date",
    "first_hearing_date",
    "next_hearing_date",
    "decision_date",
    "is_pending",
    "is_disposed",
    "hearing_dates",       # list → "date1 | date2 | ..."
    "business_dates",      # list → "date1 | date2 | ..."
    "interim_orders",      # list → "date1 | date2 | ..."
    "hearing_purposes",    # list → "date: purpose | ..."
    "full_text",           # ← was missing in previous version
]

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


# ── Credentials ───────────────────────────────────────────────────────────────

def _get_credentials() -> Credentials:
    try:
        info_str = st.secrets["gsheets"].get("service_account_info", "")
        if info_str:
            return Credentials.from_service_account_info(
                json.loads(info_str), scopes=SCOPES
            )
        json_path = st.secrets["gsheets"]["service_account_json"]
    except (KeyError, FileNotFoundError):
        json_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "service_account.json")

    return Credentials.from_service_account_file(json_path, scopes=SCOPES)


def _get_worksheet() -> gspread.Worksheet:
    creds = _get_credentials()
    gc    = gspread.authorize(creds)

    try:
        spreadsheet_id = st.secrets["gsheets"]["spreadsheet_id"]
        worksheet_name = st.secrets["gsheets"].get("worksheet_name", "Cases")
    except (KeyError, FileNotFoundError):
        spreadsheet_id = os.getenv("GSHEET_SPREADSHEET_ID", "")
        worksheet_name = os.getenv("GSHEET_WORKSHEET_NAME", "Cases")

    if not spreadsheet_id:
        raise ValueError(
            "❌ Google Sheet ID missing. "
            "Add [gsheets] spreadsheet_id to .streamlit/secrets.toml"
        )

    sh = gc.open_by_key(spreadsheet_id)
    try:
        ws = sh.worksheet(worksheet_name)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(
            title=worksheet_name,
            rows=10000,
            cols=len(SHEET_COLUMNS) + 2,
        )
    return ws


# ── Header management ─────────────────────────────────────────────────────────

def _ensure_header(ws: gspread.Worksheet):
    """
    Always keep row 1 = SHEET_COLUMNS exactly.
    If the sheet is new OR has outdated headers (e.g. missing full_text),
    rewrite row 1 so columns always match.
    """
    existing = ws.row_values(1)
    if existing != SHEET_COLUMNS:
        ws.delete_rows(1)
        ws.insert_row(SHEET_COLUMNS, index=1)


# ── Row serialisation ─────────────────────────────────────────────────────────

def _safe_str(val) -> str:
    """Convert any Python value to a clean string safe for Sheets."""
    if val is None:
        return ""
    if isinstance(val, list):
        parts = []
        for item in val:
            if isinstance(item, (dict, list)):
                parts.append(json.dumps(item, ensure_ascii=False))
            else:
                parts.append(str(item))
        return " | ".join(parts)
    if isinstance(val, dict):
        return json.dumps(val, ensure_ascii=False)
    return str(val)


def _case_to_row(case_json: dict) -> list:
    """
    Produce a list of exactly len(SHEET_COLUMNS) values,
    one per column, in column order. Missing keys → empty string.
    """
    return [_safe_str(case_json.get(col)) for col in SHEET_COLUMNS]


# ── Public API ────────────────────────────────────────────────────────────────

def save_to_sheet(case_json: dict) -> str:
    """
    Upsert a case row (matched on cnr_number).
      - CNR already exists  →  update that row in-place
      - New CNR             →  append a new row
    Returns a human-readable status string.
    """
    ws = _get_worksheet()
    _ensure_header(ws)

    cnr     = _safe_str(case_json.get("cnr_number"))
    new_row = _case_to_row(case_json)

    # Scan column A for existing CNR
    all_cnrs = ws.col_values(1)   # row 1 = "cnr_number" header

    if cnr and cnr in all_cnrs:
        row_idx = all_cnrs.index(cnr) + 1          # 1-indexed
        col_end = gspread.utils.rowcol_to_a1(row_idx, len(SHEET_COLUMNS))
        ws.update(
            f"A{row_idx}:{col_end}",
            [new_row],
            value_input_option="USER_ENTERED",
        )
        return f"✅ Updated existing row for CNR: {cnr}"
    else:
        ws.append_row(new_row, value_input_option="USER_ENTERED")
        return f"✅ Appended new row for CNR: {cnr}"
