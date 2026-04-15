"""
app.py — Court Case Processing System (fixed)
===============================================
OCR → Metadata Extraction → JSON → Save to Supabase DB + Google Sheets
"""

import streamlit as st
import pytesseract
from pdf2image import convert_from_bytes
import cv2
import numpy as np
import re
import os
import json
import tempfile

# ── Your existing extraction module ──────────────────────────────────────────
from extract_case_metadata import (
    extract_text_from_pdf,
    extract_tables_raw,
    parse_case,
)

# ── Cloud integrations ────────────────────────────────────────────────────────
try:
    from database import save_case, fetch_all_cases
    DB_AVAILABLE = True
    _DB_ERR_MSG  = ""
except Exception as _db_err:
    DB_AVAILABLE = False
    _DB_ERR_MSG  = str(_db_err)

try:
    from sheets import save_to_sheet
    SHEETS_AVAILABLE = True
    _SH_ERR_MSG      = ""
except Exception as _sh_err:
    SHEETS_AVAILABLE = False
    _SH_ERR_MSG      = str(_sh_err)


# ==========================
# PAGE CONFIG
# ==========================
st.set_page_config(
    page_title="Court Case AI System",
    page_icon="⚖️",
    layout="wide",
)

# ==========================
# CUSTOM CSS
# ==========================
st.markdown("""
<style>
.main { background-color: #0f172a; }
h1, h2, h3 { color: #38bdf8; }
.card {
    background-color: #1e293b;
    padding: 20px;
    border-radius: 15px;
    margin-bottom: 20px;
}
</style>
""", unsafe_allow_html=True)


# ==========================
# HELPERS
# ==========================

def clean_text(text: str) -> str:
    lines = text.split("\n")
    return "\n".join([re.sub(r'\s+', ' ', l.strip()) for l in lines])


def _ocr_single_page(args: tuple) -> str:
    """Process one page image — runs in a thread."""
    img, lang, psm_mode = args
    img   = np.array(img)
    gray  = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    config = f"--oem 3 --psm {psm_mode}"
    return pytesseract.image_to_string(thresh, lang=lang, config=config)


@st.cache_data(show_spinner=False)
def ocr_pdf(file_bytes: bytes, lang: str, psm_mode: int) -> str:
    """
    OCR a PDF with:
      - 150 DPI  (60% faster than 300, still accurate for court docs)
      - Parallel page processing via ThreadPoolExecutor
      - @st.cache_data so the same file is never re-processed
    """
    from concurrent.futures import ThreadPoolExecutor

    images = convert_from_bytes(file_bytes, dpi=150)
    args   = [(img, lang, psm_mode) for img in images]

    with ThreadPoolExecutor() as executor:
        results = list(executor.map(_ocr_single_page, args))

    return clean_text("\n".join(results))


def normalize_case(case_json: dict, full_text: str) -> dict:
    """
    Merge metadata JSON with OCR full_text and guarantee every
    expected key exists (None / [] for missing values).
    This prevents empty cells in Sheets / NULL in DB.
    """
    expected_keys = {
        "cnr_number": None,
        "case_type": None,
        "filing_number": None,
        "registration_number": None,
        "court_name": None,
        "court_level": None,
        "district": None,
        "state": None,
        "act_name": None,
        "section": None,
        "number_of_sections": 0,
        "filing_date": None,
        "registration_date": None,
        "first_hearing_date": None,
        "next_hearing_date": None,
        "decision_date": None,
        "is_pending": 0,
        "is_disposed": 0,
        "hearing_dates": [],
        "business_dates": [],
        "interim_orders": [],
        "hearing_purposes": [],
        "full_text": "",
    }

    result = {k: case_json.get(k, default) for k, default in expected_keys.items()}
    result["full_text"] = full_text.strip() if full_text else ""
    return result


# ==========================
# SIDEBAR
# ==========================
st.sidebar.title("⚙️ OCR Settings")
language = st.sidebar.selectbox("Language", ["eng", "eng+hin"])
psm_mode = st.sidebar.selectbox("PSM Mode", [3, 4, 6])

st.sidebar.markdown("---")
st.sidebar.title("☁️ Cloud Status")
st.sidebar.write("🗄️ Supabase DB:", "✅ Ready" if DB_AVAILABLE else "❌ Not configured")
st.sidebar.write("📊 Google Sheets:", "✅ Ready" if SHEETS_AVAILABLE else "❌ Not configured")
if not DB_AVAILABLE and _DB_ERR_MSG:
    st.sidebar.caption(f"DB: {_DB_ERR_MSG[:100]}")
if not SHEETS_AVAILABLE and _SH_ERR_MSG:
    st.sidebar.caption(f"Sheets: {_SH_ERR_MSG[:100]}")


# ==========================
# HEADER
# ==========================
st.markdown("""
<div class="card">
<h1>⚖️ Court Case Processing System</h1>
<p>OCR + Metadata Extraction + JSON Merge + Cloud Storage</p>
</div>
""", unsafe_allow_html=True)


# ==========================================================
# SECTION 1 — OCR PDF
# ==========================================================
st.markdown('<div class="card">', unsafe_allow_html=True)
st.subheader("📄 Upload Interim Order PDFs")

ocr_files = st.file_uploader(
    "Upload PDF(s)",
    type=["pdf"],
    accept_multiple_files=True,
)

ocr_text_output = ""

if ocr_files:
    def _natural_key(s):
        return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]

    ocr_files = sorted(ocr_files, key=lambda x: _natural_key(x.name))

    for file in ocr_files:
        st.info(f"Processing {file.name}…")
        ocr_text_output += f"\n--- {file.name} ---\n{ocr_pdf(file.read(), language, psm_mode)}\n"

    st.success(f"✅ OCR completed — {len(ocr_files)} file(s)")
    st.text_area("Preview OCR Output", ocr_text_output, height=250)

st.markdown('</div>', unsafe_allow_html=True)


# ==========================================================
# SECTION 2 — METADATA EXTRACTION
# ==========================================================
st.markdown('<div class="card">', unsafe_allow_html=True)
st.subheader("📊 Upload Metadata PDF")

metadata_file = st.file_uploader("Upload metadata PDF", type=["pdf"], key="metadata")
metadata_json = None

if metadata_file:
    st.info("Extracting metadata…")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(metadata_file.read())
        temp_path = tmp.name

    text         = extract_text_from_pdf(temp_path)
    raw          = extract_tables_raw(temp_path)
    metadata_json = parse_case(text, raw)

    st.success("✅ Metadata extracted")
    st.markdown("### JSON Output")
    st.json(metadata_json)

st.markdown('</div>', unsafe_allow_html=True)


# ==========================================================
# SECTION 3 — MERGE + SAVE
# ==========================================================
st.markdown('<div class="card">', unsafe_allow_html=True)
st.subheader("🔗 Merged Output + Cloud Save")

if metadata_json:
    # Always build final_json — full_text is "" if no OCR files uploaded
    final_json = normalize_case(metadata_json, ocr_text_output)

    if ocr_text_output:
        st.success("✅ Merged: metadata + OCR full_text")
    else:
        st.warning("⚠️ No OCR PDFs uploaded — full_text will be empty")

    st.markdown("### Final JSON (what gets saved)")
    st.json(final_json)

    # ── Download ──────────────────────────────────────────────────────
    st.download_button(
        "📥 Download JSON",
        data=json.dumps(final_json, indent=2, ensure_ascii=False),
        file_name=f"{final_json.get('cnr_number', 'case')}.json",
        mime="application/json",
    )

    st.markdown("---")
    st.markdown("### ☁️ Save to Cloud")

    col1, col2 = st.columns(2)

    # ── Supabase ──────────────────────────────────────────────────────
    with col1:
        st.markdown("**🗄️ Supabase Database**")
        if DB_AVAILABLE:
            if st.button("💾 Save to Database", use_container_width=True):
                with st.spinner("Saving to Supabase…"):
                    try:
                        save_case(final_json)
                        st.success(f"✅ Saved! CNR: {final_json.get('cnr_number')}")
                        ft_len = len(final_json.get("full_text", ""))
                        st.caption(f"full_text length: {ft_len} characters")
                    except Exception as e:
                        st.error(f"❌ DB Error: {e}")
        else:
            st.warning("⚠️ Supabase not configured. See Setup Guide below.")

    # ── Google Sheets ─────────────────────────────────────────────────
    with col2:
        st.markdown("**📊 Google Sheets**")
        if SHEETS_AVAILABLE:
            if st.button("📤 Save to Google Sheet", use_container_width=True):
                with st.spinner("Saving to Google Sheets…"):
                    try:
                        msg = save_to_sheet(final_json)
                        st.success(msg)
                        ft_len = len(final_json.get("full_text", ""))
                        st.caption(f"full_text length: {ft_len} characters")
                    except Exception as e:
                        st.error(f"❌ Sheets Error: {e}")
        else:
            st.warning("⚠️ Google Sheets not configured. See Setup Guide below.")

    # ── Save BOTH ─────────────────────────────────────────────────────
    if DB_AVAILABLE and SHEETS_AVAILABLE:
        st.markdown("---")
        if st.button("🚀 Save to BOTH (DB + Sheet)", use_container_width=True, type="primary"):
            with st.spinner("Saving everywhere…"):
                errors = []
                try:
                    save_case(final_json)
                    st.success("✅ Saved to Supabase DB")
                except Exception as e:
                    errors.append(f"DB: {e}")
                try:
                    msg = save_to_sheet(final_json)
                    st.success(msg)
                except Exception as e:
                    errors.append(f"Sheets: {e}")
                for err in errors:
                    st.error(f"❌ {err}")

else:
    st.info("📌 Upload a Metadata PDF to enable saving (OCR PDFs optional)")

st.markdown('</div>', unsafe_allow_html=True)


# ==========================================================
# SECTION 4 — VIEW ALL SAVED CASES
# ==========================================================
if DB_AVAILABLE:
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("🗂️ View All Saved Cases")

    if st.button("🔄 Load All Cases from Database"):
        with st.spinner("Fetching from Supabase…"):
            try:
                cases = fetch_all_cases()
                if cases:
                    import pandas as pd
                    st.caption(f"Total: {len(cases)} case(s) in database")

                    # Summary table (no full_text — too long)
                    summary_cols = [
                        "cnr_number", "case_type", "district", "state",
                        "filing_date", "next_hearing_date", "is_pending",
                        "act_name", "section",
                    ]
                    df = pd.DataFrame(cases)
                    df_show = df[[c for c in summary_cols if c in df.columns]]
                    st.dataframe(df_show, use_container_width=True)

                    # Per-case full_text viewer
                    st.markdown("#### 📄 Full Text per Case")
                    for case in cases:
                        cnr = case.get("cnr_number", "Unknown")
                        ft  = case.get("full_text") or ""
                        ok  = bool(ft.strip())
                        with st.expander(f"{'✅' if ok else '❌ empty'} {cnr}"):
                            if ok:
                                st.text_area(
                                    "full_text",
                                    ft,
                                    height=300,
                                    key=f"ft_{cnr}",
                                )
                                st.caption(f"{len(ft)} characters")
                            else:
                                st.warning(
                                    "full_text is empty. Make sure you:\n"
                                    "1. Upload the Interim Order PDFs (Section 1)\n"
                                    "2. Upload the Metadata PDF (Section 2)\n"
                                    "3. Click Save again."
                                )
                else:
                    st.info("No cases saved yet.")
            except Exception as e:
                st.error(f"❌ Error loading cases: {e}")

    st.markdown('</div>', unsafe_allow_html=True)


# ==========================================================
# SECTION 5 — SETUP GUIDE
# ==========================================================
with st.expander("📖 Setup Guide"):
    st.markdown("""
## 1️⃣ Supabase — run this SQL once

```sql
CREATE TABLE IF NOT EXISTS court_cases (
    cnr_number            TEXT PRIMARY KEY,
    case_type             TEXT,
    filing_number         TEXT,
    registration_number   TEXT,
    court_name            TEXT,
    court_level           TEXT,
    district              TEXT,
    state                 TEXT,
    act_name              TEXT,
    section               TEXT,
    number_of_sections    INT,
    filing_date           TEXT,
    registration_date     TEXT,
    first_hearing_date    TEXT,
    next_hearing_date     TEXT,
    decision_date         TEXT,
    is_pending            INT,
    is_disposed           INT,
    hearing_dates         JSONB,
    business_dates        JSONB,
    interim_orders        JSONB,
    hearing_purposes      JSONB,
    full_text             TEXT,
    created_at            TIMESTAMPTZ DEFAULT NOW()
);

-- If table already existed without full_text:
ALTER TABLE court_cases ADD COLUMN IF NOT EXISTS full_text TEXT;
```

## 2️⃣ secrets.toml  (.streamlit/secrets.toml)

```toml
[supabase]
url = "https://xxxx.supabase.co"
key = "your-anon-public-key"

[gsheets]
spreadsheet_id = "your-sheet-id-from-url"
worksheet_name = "Cases"
service_account_json = "service_account.json"
```

## 3️⃣ Install dependencies

```bash
pip install streamlit pytesseract pdf2image opencv-python numpy pdfplumber supabase gspread google-auth pandas
```
""")