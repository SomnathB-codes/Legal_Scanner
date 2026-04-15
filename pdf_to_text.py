import os
import pytesseract
from pdf2image import convert_from_path
import cv2
import numpy as np
import re

# ==========================
# 📁 CONFIG
# ==========================
INPUT_FOLDER = "Pending case/Tripura/TRDL01-000174-2025/Interim orders"
OUTPUT_FILE = "output_txt/interim_orders_output_TRDL01-000174-2025.txt"


# ==========================
# 🔢 NATURAL SORT
# ==========================
def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower()
            for text in re.split(r'(\d+)', s)]


# ==========================
# 🧹 CLEAN TEXT
# ==========================
def clean_text(text):
    lines = text.split("\n")
    cleaned = []

    for line in lines:
        line = line.strip()

        if len(line) == 0:
            cleaned.append("")
            continue

        line = re.sub(r'\s+', ' ', line)
        cleaned.append(line)

    final_text = ""
    for i, line in enumerate(cleaned):
        if i > 0:
            prev = cleaned[i - 1]

            if prev and not prev.endswith(('.', ':', '?')):
                final_text = final_text.rstrip() + " " + line + "\n"
                continue

        final_text += line + "\n"

    return final_text


# ==========================
# 📄 FORMAT FIX
# ==========================
def format_paragraphs(text):
    text = re.sub(r'(Order:)', r'\1\n', text)
    text = re.sub(r'(Present)', r'\n\1', text)
    text = re.sub(r'(\d)(Fix)', r'\1\nFix', text)
    return text


# ==========================
# 📄 OCR FUNCTION
# ==========================
def ocr_pdf(pdf_path):
    images = convert_from_path(pdf_path, dpi=300)
    full_text = ""

    for img in images:
        img = np.array(img)

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)

        _, thresh = cv2.threshold(
            gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )

        custom_config = r'--oem 3 --psm 4'
        text = pytesseract.image_to_string(thresh, config=custom_config)

        full_text += text + "\n"

    text = clean_text(full_text)
    text = format_paragraphs(text)

    return text


# ==========================
# 🚀 MAIN PROCESS
# ==========================
def main():
    os.makedirs("output_txt", exist_ok=True)

    files = sorted(os.listdir(INPUT_FOLDER), key=natural_sort_key)

    final_output = ""

    for file in files:
        if file.endswith(".pdf"):
            print(f"Processing: {file}")

            pdf_path = os.path.join(INPUT_FOLDER, file)

            extracted_text = ocr_pdf(pdf_path)

            name = os.path.splitext(file)[0]

            formatted = f"""
---------------------------------------------------------{name}--------------------------------------------------------------------------

{extracted_text}

"""

            final_output += formatted

    # save output
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(final_output)

    print("\n✅ DONE! Output saved at:", OUTPUT_FILE)


# ==========================
# ▶️ RUN
# ==========================
if __name__ == "__main__":
    main()