"""
DRISHTI backend - FAST Gemini Vision extraction.

Pipeline:

    Photos
       ↓
    Lightweight image preparation
       ↓
    ONE Gemini Vision call
       ↓
    Structured declaration extraction
       ↓
    Date/value validation
       ↓
    Optional OCR fallback
       ↓
    Deterministic compliance rules

The previous two-pass Gemini + tiled-image pipeline has
intentionally been removed for speed.
"""

import json
import os
import re
import traceback
from typing import Optional
from reportlab.lib.pagesizes import A4
from io import BytesIO

from fastapi.responses import StreamingResponse

from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER,TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont



import easyocr

from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from google import genai
from google.genai import types

from pydantic import BaseModel, Field


from image_prep import (
    build_gemini_image_set,
    enhance_for_ocr,
    format_ocr_hint,
)

from rules import check_compliance


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="DRISHTI Compliance API",
    version="4.0-fast",
)
@app.get("/health")
def health():
    return {"message": "Backend is working"}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# GEMINI
# ============================================================

GEMINI_API_KEY = os.getenv(
    "GEMINI_API_KEY",
    ""
).strip()


GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")


gemini_client = (
    genai.Client(
        api_key=GEMINI_API_KEY
    )
    if GEMINI_API_KEY
    else None
)


# ============================================================
# OCR
# ============================================================

# IMPORTANT:
#
# Default is OFF for speed.
#
# To enable OCR fallback:
#
# PowerShell:
# $env:DRISHTI_OCR="1"
#
# Then restart FastAPI.
#
USE_OCR = (
    os.getenv(
        "DRISHTI_OCR",
        "0"
    ).strip().lower()
    in {"1", "true", "yes", "on"}
)


reader = None


def get_ocr_reader():
    """
    Lazy-load EasyOCR.

    This prevents EasyOCR from delaying backend startup
    when OCR is not being used.
    """

    global reader

    if reader is None:

        print(
            "Loading EasyOCR..."
        )

        reader = easyocr.Reader(
            ["en"],
            gpu=False
        )

        print(
            "EasyOCR loaded."
        )

    return reader


# ============================================================
# LOGIN MODEL
# ============================================================

class LoginRequest(BaseModel):
    role: str
    user_id: str
    password: str
class ComplianceRequest(BaseModel):
    fields: dict
    context: dict = Field(default_factory=dict)
class OfficerCreateRequest(BaseModel):
    admin_id: str
    admin_password: str
    officer_id: str
    officer_password: str


class OfficerDeleteRequest(BaseModel):
    admin_id: str
    admin_password: str
    officer_id: str

# ============================================================
# GEMINI STRUCTURED OUTPUT
# ============================================================

class FieldResult(BaseModel):

    value: str = Field(
        description=(
            "Final value selected from visible "
            "package evidence. Empty when not "
            "confidently supported."
        )
    )

    confidence: float = Field(
        description=(
            "Confidence from 0 to 1."
        )
    )

    evidence: str = Field(
        description=(
            "Short explanation of the "
            "visible evidence supporting "
            "the value."
        )
    )

class DeclarationResult(BaseModel):

    product_name: FieldResult

    mrp: FieldResult

    net_quantity: FieldResult

    manufacturer: FieldResult

    consumer_care: FieldResult

    manufacturing_date: FieldResult

    country_of_origin: FieldResult

    unit_sale_price: FieldResult

    best_before: FieldResult

# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():

    return {
        "success": True,
        "message": "DRISHTI backend is working!"
    }


# ============================================================
# LOGIN
# ============================================================

OFFICERS_FILE = os.path.join(
    os.path.dirname(__file__),
    "officers.json",
)


def _normalize_officer_id(officer_id: str) -> str:
    return officer_id.strip().upper().replace("-", "")


def _load_officers():
    if not os.path.exists(OFFICERS_FILE):
        officers = {
            "OFF001": "1234",
            "OFF002": "1234",
            "OFF003": "1234",
        }

        with open(
            OFFICERS_FILE,
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                officers,
                file,
                indent=2,
            )

        return officers

    try:
        with open(
            OFFICERS_FILE,
            "r",
            encoding="utf-8",
        ) as file:
            data = json.load(file)

        return data if isinstance(data, dict) else {}

    except (json.JSONDecodeError, OSError):
        return {}


def _save_officers(officers):
    with open(
        OFFICERS_FILE,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            officers,
            file,
            indent=2,
        )


def _is_admin(
    admin_id: str,
    admin_password: str,
) -> bool:
    return (
        admin_id.strip().upper() == "ADM001"
        and admin_password == "1234"
    )

@app.post("/login")
def login(data: LoginRequest):

    if data.role == "admin":

        valid = (
            data.user_id.strip().upper() == "ADM001"
            and data.password == "1234"
        )

    elif data.role == "officer":

        officers = _load_officers()

        officer_id = _normalize_officer_id(
            data.user_id
        )

        valid = (
            officers.get(officer_id)
            == data.password
        )

    else:

        valid = False

    if valid:

        return {
            "success": True,
            "message": "Login successful",
            "role": data.role,
            "user_id": data.user_id,
        }

    return {
        "success": False,
        "message": "Invalid ID or password",
    }
@app.get("/admin/officers")
def get_officers():

    officers = _load_officers()

    return {
        "success": True,
        "officers": [
            f"{officer_id[:3]}-{officer_id[3:]}"
            for officer_id in officers.keys()
        ],
    }


@app.post("/admin/officers")
def add_officer(
    data: OfficerCreateRequest,
):

    if not _is_admin(
        data.admin_id,
        data.admin_password,
    ):
        return {
            "success": False,
            "message": "Admin authentication failed",
        }

    officer_id = _normalize_officer_id(
        data.officer_id
    )

    if not re.fullmatch(
        r"OFF\d{3}",
        officer_id,
    ):
        return {
            "success": False,
            "message": "Officer ID must use the OFF-001 format",
        }

    if not data.officer_password.strip():
        return {
            "success": False,
            "message": "Officer password cannot be empty",
        }

    officers = _load_officers()

    if officer_id in officers:
        return {
            "success": False,
            "message": "Officer already exists",
        }

    officers[officer_id] = data.officer_password

    _save_officers(officers)

    return {
        "success": True,
        "message": "Officer added successfully",
        "officer_id": (
            f"{officer_id[:3]}-{officer_id[3:]}"
        ),
    }


@app.delete("/admin/officers")
def delete_officer(
    data: OfficerDeleteRequest,
):

    if not _is_admin(
        data.admin_id,
        data.admin_password,
    ):
        return {
            "success": False,
            "message": "Admin authentication failed",
        }

    officer_id = _normalize_officer_id(
        data.officer_id
    )

    officers = _load_officers()

    if officer_id not in officers:
        return {
            "success": False,
            "message": "Officer not found",
        }

    del officers[officer_id]

    _save_officers(officers)

    return {
        "success": True,
        "message": "Officer removed successfully",
    }
# ============================================================
# CHECK COMPLIANCE
# ============================================================

@app.post("/check-compliance")
def check_compliance_endpoint(data: ComplianceRequest):
    compliance = check_compliance(
    data.fields,
    None,
    data.context
)

    return {
        "success": True,
        "extracted": data.fields,
        "context": data.context,
        "compliance": compliance,
    }
class ReportRequest(BaseModel):
    fields: dict
    context: dict = Field(default_factory=dict)
    compliance: dict


@app.post("/generate-report")
def generate_report(data: ReportRequest):
    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36,
        title="DRISHTI Legal Metrology Inspection Report",
        author="DRISHTI",
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Title"],
        fontName="Helvetica",
        fontSize=20,
        leading=24,
        textColor=colors.HexColor("#17365D"),
        spaceAfter=4,
    )

    subtitle_style = ParagraphStyle(
        "ReportSubtitle",
        parent=styles["Normal"],
        fontSize=9,
        textColor=colors.HexColor("#666666"),
        spaceAfter=16,
    )

    section_style = ParagraphStyle(
        "SectionHeading",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=15,
        textColor=colors.HexColor("#17365D"),
        spaceBefore=10,
        spaceAfter=8,
    )

    normal_style = ParagraphStyle(
        "ReportNormal",
        parent=styles["Normal"],
        fontSize=8.5,
        leading=11,
    )

    small_style = ParagraphStyle(
        "ReportSmall",
        parent=styles["Normal"],
        fontSize=7.5,
        leading=10,
        textColor=colors.HexColor("#666666"),
    )

    story = []

    # ---------------------------------------------------------
    # HEADER
    # ---------------------------------------------------------
    story.append(Paragraph("DRISHTI", title_style))
    story.append(
        Paragraph(
            "Legal Metrology Compliance Inspection Report",
            subtitle_style,
        )
    )

    # ---------------------------------------------------------
    # STATUS SUMMARY
    # ---------------------------------------------------------
    checks = data.compliance.get("checks", [])
    overall_status = data.compliance.get(
        "overall_status",
        "NEEDS REVIEW",
    )
    compliance_score = data.compliance.get("score", 0)
    score_classification = data.compliance.get(
        "score_classification",
        "Compliance Score",
    )
    pass_count = sum(
        1 for check in checks
        if check.get("status") == "PASS"
    )

    review_count = sum(
        1 for check in checks
        if check.get("status") == "NEEDS REVIEW"
    )

    failure_count = sum(
        1 for check in checks
        if check.get("status") == "POSSIBLE NON-COMPLIANCE"
    )

    status_color = "#D97706"

    if overall_status == "NO NON-COMPLIANCE DETECTED":
        status_color = "#15803D"
    elif overall_status == "POSSIBLE NON-COMPLIANCE":
        status_color = "#B91C1C"

    status_table = Table(
        [
            [
                Paragraph(
                    "<b>OVERALL STATUS</b>",
                    normal_style,
                ),
                Paragraph(
                    f"<b>{overall_status}</b>",
                    ParagraphStyle(
                        "StatusText",
                        parent=normal_style,
                        textColor=colors.HexColor(status_color),
                        alignment=TA_RIGHT if "TA_RIGHT" in globals() else TA_LEFT,
                    ),
                ),
            ],
            [
                Paragraph(
                    "<b>COMPLIANCE SCORE</b>",
                    normal_style,
                ),
                Paragraph(
                    f"<b>{compliance_score}/100</b> — "
                    f"{score_classification}",
                    normal_style,
                ),
            ],
            [
                Paragraph(
                    "Inspection summary",
                    small_style,
                ),
                Paragraph(
                    f"{pass_count} passed  •  "
                    f"{review_count} need review"
                    + (
                        f"  •  {failure_count} possible non-compliance"
                        if failure_count
                        else ""
                    ),
                    small_style,
                ),
            ],
        ],
        colWidths=[240, 240],
    )

    status_table.setStyle(
        TableStyle(
            [
                (
                    "BACKGROUND",
                    (0, 0),
                    (-1, 0),
                    colors.HexColor("#F4F7FA"),
                ),
                (
                    "BOX",
                    (0, 0),
                    (-1, -1),
                    0.7,
                    colors.HexColor("#D5DCE3"),
                ),
                (
                    "INNERGRID",
                    (0, 0),
                    (-1, -1),
                    0.4,
                    colors.HexColor("#E5E7EB"),
                ),
                (
                    "LEFTPADDING",
                    (0, 0),
                    (-1, -1),
                    10,
                ),
                (
                    "RIGHTPADDING",
                    (0, 0),
                    (-1, -1),
                    10,
                ),
                (
                    "TOPPADDING",
                    (0, 0),
                    (-1, -1),
                    8,
                ),
                (
                    "BOTTOMPADDING",
                    (0, 0),
                    (-1, -1),
                    8,
                ),
            ]
        )
    )

    story.append(status_table)
    story.append(Spacer(1, 14))

    # ---------------------------------------------------------
    # EXTRACTED DECLARATIONS
    # ---------------------------------------------------------
    story.append(
        Paragraph(
            "Extracted Declarations",
            section_style,
        )
    )

    field_labels = {
    "product_name": "Product Name",
    "mrp": "Maximum Retail Price",
    "net_quantity": "Net Quantity",
    "manufacturer": "Manufacturer / Packer",
    "consumer_care": "Consumer Care Details",
    "manufacturing_date": "Manufacturing / Packing Date",
    "country_of_origin": "Country of Origin",
    "unit_sale_price": "Unit Sale Price",
    "best_before": "Best Before / Use By",
}

    declaration_rows = [
        [
            Paragraph("<b>Field</b>", normal_style),
            Paragraph("<b>Extracted Value</b>", normal_style),
        ]
    ]

    for key, label in field_labels.items():
        value = data.fields.get(key, "")

        if value is None or str(value).strip() == "":
            value = "Not extracted"

        declaration_rows.append(
            [
                Paragraph(label, normal_style),
                Paragraph(str(value), normal_style),
            ]
        )

    declaration_table = Table(
        declaration_rows,
        colWidths=[180, 300],
        repeatRows=1,
    )

    declaration_table.setStyle(
        TableStyle(
            [
                (
                    "BACKGROUND",
                    (0, 0),
                    (-1, 0),
                    colors.HexColor("#EEF2F6"),
                ),
                (
                    "TEXTCOLOR",
                    (0, 0),
                    (-1, 0),
                    colors.HexColor("#17365D"),
                ),
                (
                    "GRID",
                    (0, 0),
                    (-1, -1),
                    0.45,
                    colors.HexColor("#D5DCE3"),
                ),
                (
                    "VALIGN",
                    (0, 0),
                    (-1, -1),
                    "TOP",
                ),
                (
                    "LEFTPADDING",
                    (0, 0),
                    (-1, -1),
                    7,
                ),
                (
                    "RIGHTPADDING",
                    (0, 0),
                    (-1, -1),
                    7,
                ),
                (
                    "TOPPADDING",
                    (0, 0),
                    (-1, -1),
                    6,
                ),
                (
                    "BOTTOMPADDING",
                    (0, 0),
                    (-1, -1),
                    6,
                ),
            ]
        )
    )

    story.append(declaration_table)

    # ---------------------------------------------------------
    # COMPLIANCE CHECKS
    # ---------------------------------------------------------
    story.append(
        Paragraph(
            "Compliance Checks",
            section_style,
        )
    )

    compliance_rows = [
        [
            Paragraph("<b>Requirement</b>", normal_style),
            Paragraph("<b>Status</b>", normal_style),
            Paragraph("<b>Finding</b>", normal_style),
        ]
    ]

    for check in checks:
        field = check.get("field", "Unknown")
        status = check.get("status", "NEEDS REVIEW")
        reason = check.get("reason", "")

        if status == "PASS":
            status_text = "PASS"
        elif status == "POSSIBLE NON-COMPLIANCE":
            status_text = "POSSIBLE NON-COMPLIANCE"
        else:
            status_text = "NEEDS REVIEW"

        compliance_rows.append(
            [
                Paragraph(str(field), normal_style),
                Paragraph(
                    f"<b>{status_text}</b>",
                    ParagraphStyle(
                        "CheckStatus",
                        parent=normal_style,
                        textColor=colors.HexColor(
                            "#15803D"
                            if status == "PASS"
                            else (
                                "#B91C1C"
                                if status == "POSSIBLE NON-COMPLIANCE"
                                else "#D97706"
                            )
                        ),
                    ),
                ),
                Paragraph(str(reason), normal_style),
            ]
        )

    compliance_table = Table(
        compliance_rows,
        colWidths=[155, 125, 200],
        repeatRows=1,
    )

    compliance_table.setStyle(
        TableStyle(
            [
                (
                    "BACKGROUND",
                    (0, 0),
                    (-1, 0),
                    colors.HexColor("#EEF2F6"),
                ),
                (
                    "GRID",
                    (0, 0),
                    (-1, -1),
                    0.45,
                    colors.HexColor("#D5DCE3"),
                ),
                (
                    "VALIGN",
                    (0, 0),
                    (-1, -1),
                    "TOP",
                ),
                (
                    "LEFTPADDING",
                    (0, 0),
                    (-1, -1),
                    7,
                ),
                (
                    "RIGHTPADDING",
                    (0, 0),
                    (-1, -1),
                    7,
                ),
                (
                    "TOPPADDING",
                    (0, 0),
                    (-1, -1),
                    6,
                ),
                (
                    "BOTTOMPADDING",
                    (0, 0),
                    (-1, -1),
                    6,
                ),
            ]
        )
    )

    story.append(compliance_table)

    # ---------------------------------------------------------
    # CONTEXT
    # ---------------------------------------------------------
    story.append(
        Paragraph(
            "Inspection Context",
            section_style,
        )
    )

    category = data.context.get("category", "general")
    imported = data.context.get("imported", False)
    wholesale = data.context.get("wholesale", False)

    context_rows = [
        [
            Paragraph("<b>Category</b>", normal_style),
            Paragraph(str(category), normal_style),
        ],
        [
            Paragraph("<b>Imported</b>", normal_style),
            Paragraph("Yes" if imported else "No", normal_style),
        ],
        [
            Paragraph("<b>Wholesale</b>", normal_style),
            Paragraph("Yes" if wholesale else "No", normal_style),
        ],
    ]

    context_table = Table(
        context_rows,
        colWidths=[180, 300],
    )

    context_table.setStyle(
        TableStyle(
            [
                (
                    "GRID",
                    (0, 0),
                    (-1, -1),
                    0.45,
                    colors.HexColor("#D5DCE3"),
                ),
                (
                    "BACKGROUND",
                    (0, 0),
                    (0, -1),
                    colors.HexColor("#F7F8FA"),
                ),
                (
                    "VALIGN",
                    (0, 0),
                    (-1, -1),
                    "TOP",
                ),
                (
                    "LEFTPADDING",
                    (0, 0),
                    (-1, -1),
                    7,
                ),
                (
                    "RIGHTPADDING",
                    (0, 0),
                    (-1, -1),
                    7,
                ),
                (
                    "TOPPADDING",
                    (0, 0),
                    (-1, -1),
                    6,
                ),
                (
                    "BOTTOMPADDING",
                    (0, 0),
                    (-1, -1),
                    6,
                ),
            ]
        )
    )

    story.append(context_table)
    story.append(Spacer(1, 14))

    # ---------------------------------------------------------
    # DISCLAIMER
    # ---------------------------------------------------------
    disclaimer = data.compliance.get(
        "disclaimer",
        "This report is an AI-assisted inspection aid and does not replace official legal verification."
    )

    story.append(
        Paragraph(
            f"<b>Note:</b> {disclaimer}",
            small_style,
        )
    )

    doc.build(story)

    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={
            "Content-Disposition": "attachment; filename=drishti_inspection_report.pdf"
        },
    )
# ============================================================
# GEMINI CONTENT BUILDER
# ============================================================

def _gemini_contents(
    prompt: str,
    image_data
):

    contents = [prompt]

    for image_bytes, mime_type in image_data:

        contents.append(
            types.Part.from_bytes(
                data=image_bytes,
                mime_type=mime_type,
            )
        )

    return contents


# ============================================================
# FAST GEMINI EXTRACTION
# ============================================================

def gemini_extract(
    image_data,
    ocr_hint_text: str = ""
):
    """
    ONE Gemini call.

    Gemini directly performs:
    - visual reading
    - label/value association
    - field extraction
    - uncertainty handling

    There is no Pass 1 / Pass 2.
    """

    if not gemini_client:

        print(
            "Gemini unavailable: "
            "GEMINI_API_KEY not configured."
        )

        return None


    prompt = f"""
You are DRISHTI, an intelligent packaged-commodity
inspection assistant.

Analyze the supplied photographs of the SAME product package.

Your task is to extract the visible package declarations
into exactly nine structured fields.

IMPORTANT:
Use the actual photographs as the PRIMARY evidence.

OCR hints are secondary evidence only.

Do NOT invent, infer, or repair unreadable text.

============================================================
FIELDS
============================================================

1. PRODUCT NAME
2. MRP
3. NET QUANTITY
4. MANUFACTURER / PACKER
5. CONSUMER CARE
6. MANUFACTURING / PACKING DATE
7. COUNTRY OF ORIGIN
8. UNIT SALE PRICE
9. BEST BEFORE / USE BY

============================================================
GENERAL RULES
============================================================

- Read the actual visible package.
- Combine multiple photographs when they show different
  sides of the same package.
- A value may appear separately from its label.
- Use spatial proximity, layout, typography and wording.
- Do not use general product knowledge to fill missing data.
- If a field is not clearly visible, return an empty value.
- Never fabricate a value merely to complete the schema.
- Preserve the original wording as much as practical.
- Do not make a legal compliance decision.

============================================================
MRP
============================================================

Look for:

- MRP
- M.R.P.
- MRP Rs.
- Maximum Retail Price
- Rs.
- ₹

Very important:

A printed label and a separately printed price may be
physically close but not on the same line.

For example:

MRP Rs.
38/-

should be understood as MRP = ₹38/-.

Do NOT confuse:

- MRP
- batch number
- phone number
- licence number
- quantity
- expiry date

============================================================
NET QUANTITY
============================================================

Look for:

- Net Quantity
- Net Qty
- Net Weight
- Net Wt.
- Quantity

Examples:

50 ml
100 g
1 kg
250 ml

Only select a quantity when the package context supports
that it represents the product quantity.

============================================================
MANUFACTURER / PACKER
============================================================

Look for:

- Manufactured by
- Manufactured at
- Mfg. by
- Packed by
- Packer
- Repacked by
- manufacturer/unit declarations

IMPORTANT:

"Marketed By" does NOT automatically mean manufacturer.

If the package says:

Repacked by:
COMPANY NAME
ADDRESS

then return that as the manufacturer/packer field,
while preserving the fact that it is a repacker.

If several companies are listed and the package contains
an instruction explaining how to identify the manufacturing
unit from the batch number, do NOT arbitrarily select one
unless the required batch-prefix relationship is clearly
visible.

============================================================
CONSUMER CARE
============================================================

Look for:

- Consumer Care
- Consumer Care Cell
- Customer Care
- Care Cell
- Customer Service
- Toll Free
- phone number
- email associated with customer service

Return the associated contact details when clearly linked.

Do NOT treat an unrelated phone number as consumer care.

============================================================
MANUFACTURING / PACKING DATE
============================================================

Look for:

- Mfg. Date
- Mfg Date
- MFD
- Manufacturing Date
- Packed
- PKD
- Packing Date
- Date of Packing
- Date of Repacking
- Date of Rpg.

CRITICAL DATE RULE:

Do NOT confuse:

- Batch No.
- Manufacturing / Packing Date
- Repacking Date
- Expiry Date
- Use Before
- Best Before

For example, if the package visually shows:

Date of Rpg.
FEB-26

Expiry Date
JAN-28

then:

manufacturing_date = FEB-26

NOT JAN-28.

DOT-MATRIX / STAMPED DATE RULE:

If a date is faint:

1. Look at its physical position.
2. Find the nearest date label.
3. Use the exact visible characters.
4. Never change a digit.
5. Never infer a missing digit.
6. If multiple dates could be candidates, return empty.
7. If a date is unclear, return empty.

For example:

Visible: FEB-26

Never output:

FEB-28
FEB-25
FEB-2026

unless those exact characters are visibly supported.

============================================================
BEST BEFORE / USE BY / EXPIRY
============================================================

Look carefully for:

- Best Before
- Best Before End
- Use By
- Use Before
- Expiry
- Expiry Date
- EXP
- Exp.
- BB

IMPORTANT:

This field is separate from Manufacturing / Packing Date.

If the package shows both:

Mfg. Date: FEB-26
Expiry Date: JAN-28

then:

manufacturing_date = FEB-26
best_before = JAN-28

Do NOT confuse the two dates.

Read the exact visible value and preserve its original
format as much as practical.

If the expiry/best-before information is visible anywhere
on any supplied photograph, extract it even if it is
printed separately from the product name or other
declarations.

If it is unclear or unreadable, return an empty value.
Do not guess.



============================================================
PRODUCT NAME
============================================================

Use the prominent product title.

Combine adjacent lines when they clearly form one title.

For example:

CASTOR
OIL I.P.

should become:

CASTOR OIL I.P.

Do not use an unrelated licence number, batch number,
manufacturer name or address as the product name.

============================================================
OCR HINTS
============================================================

These are only supporting clues:

{ocr_hint_text}

============================================================
FINAL SAFETY CHECK
============================================================

Before returning each field:

- Is it actually visible?
- Is it associated with the correct label?
- Could it be confused with another number?
- Did I accidentally convert an expiry date into a
  manufacturing date?
- Did I accidentally convert "Marketed By" into
  "Manufacturer"?
- Did I invent anything?

If uncertain, return:

value = ""

with low confidence and an explanation.

Return ONLY the requested structured JSON.
"""


    try:

        print(
            "Calling Gemini:",
            GEMINI_MODEL
        )

        response = (
            gemini_client
            .models
            .generate_content(
                model=GEMINI_MODEL,
                contents=_gemini_contents(
                    prompt,
                    image_data
                ),
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=DeclarationResult,
                    temperature=0,
                ),
            )
        )


        result = (
            DeclarationResult
            .model_validate_json(
                response.text
            )
            .model_dump()
        )


        print(
            "Gemini extraction completed."
        )

        return result


    except Exception as exc:

        print("AI service is currently experiencing high demand. Please try again shortly.")

        traceback.print_exc()

        return None

    # --------------------------------------------------------
    # FALLBACK GEMINI MODEL
    # --------------------------------------------------------
    


# ============================================================
# OCR
# ============================================================

def extract_with_ocr(
    raw_images
):

    all_items = []

    if not raw_images:
        return all_items


    ocr_reader = get_ocr_reader()


    for image_index, (
        image_bytes,
        _mime_type
    ) in enumerate(
        raw_images,
        start=1
    ):

        try:

            enhanced = (
                enhance_for_ocr(
                    image_bytes
                )
            )


            detections = (
                ocr_reader.readtext(
                    enhanced,
                    detail=1,
                    paragraph=False,
                    mag_ratio=1.0,
                    text_threshold=0.55,
                    low_text=0.25,
                    link_threshold=0.30,
                )
            )


            for detection in detections:

                if len(detection) < 3:
                    continue


                box, text, confidence = (
                    detection
                )


                text = str(
                    text
                ).strip()


                if not text:
                    continue


                converted_box = [
                    [
                        int(point[0]),
                        int(point[1])
                    ]
                    for point in box
                ]


                all_items.append(
                    {
                        "text": text,
                        "confidence": round(
                            float(confidence),
                            4
                        ),
                        "box": converted_box,
                        "source": (
                            f"original-{image_index}"
                        ),
                    }
                )


        except Exception as exc:

            print(
                f"OCR ERROR on image "
                f"{image_index}:",
                repr(exc)
            )


    return all_items


# ============================================================
# OCR BASIC FALLBACK
# ============================================================

def basic_extraction(
    ocr_items
):

    fields = {
        "product_name": "",
        "mrp": "",
        "net_quantity": "",
        "manufacturer": "",
        "consumer_care": "",
        "manufacturing_date": "",
    }


    texts = [
        str(
            item.get(
                "text",
                ""
            )
        ).strip()

        for item in ocr_items

        if str(
            item.get(
                "text",
                ""
            )
        ).strip()
    ]


    combined = "\n".join(
        texts
    )


    # --------------------------------------------------------
    # MRP
    # --------------------------------------------------------

    match = re.search(
        r"""
        (?:mrp|maximum\s+retail\s+price)
        \s*
        (?:rs\.?|₹|inr)?
        \s*
        [:.-]?
        \s*
        ([0-9]{1,7}(?:\.\d+)?)
        """,
        combined,
        re.I | re.X,
    )


    if match:

        fields["mrp"] = (
            "₹"
            + match.group(1)
        )


    # --------------------------------------------------------
    # QUANTITY
    # --------------------------------------------------------

    match = re.search(
        r"""
        (?:net\s*
        (?:quantity|qty|weight|wt))
        \s*
        [:.-]?
        \s*
        ([0-9]+(?:\.[0-9]+)?)
        \s*
        (kg|g|mg|l|ml|litre|liter|pcs|pc|pieces|units?)
        \b
        """,
        combined,
        re.I | re.X,
    )


    if match:

        fields["net_quantity"] = (
            f"{match.group(1)} "
            f"{match.group(2)}"
        )

    else:

        quantity = re.search(
            r"""
            \b
            ([0-9]+(?:\.[0-9]+)?)
            \s*
            (kg|g|mg|l|ml|litre|liter|pcs|pc|pieces|units?)
            \b
            """,
            combined,
            re.I | re.X,
        )


        if quantity:

            fields["net_quantity"] = (
                quantity.group(0)
            )


    # --------------------------------------------------------
    # CONSUMER CARE
    # --------------------------------------------------------

    phone = re.search(
        r"""
        (?:
            consumer\s*care|
            customer\s*care|
            care\s*cell|
            toll\s*free
        )
        .*?
        (
            (?:\+91[\s-]?)?
            [6-9][0-9]{2}
            [\s-]?
            [0-9]{3}
            [\s-]?
            [0-9]{4}
        )
        """,
        combined,
        re.I | re.S | re.X,
    )


    if phone:

        fields["consumer_care"] = (
            phone.group(1)
        )

    else:

        toll = re.search(
            r"\b1[0-9]{3}"
            r"[\s-]?[0-9]{3}"
            r"[\s-]?[0-9]{4}\b",
            combined
        )


        if toll:

            fields["consumer_care"] = (
                toll.group(0)
            )


    # --------------------------------------------------------
    # MANUFACTURING / PACKING DATE
    # --------------------------------------------------------

    date_match = re.search(
        r"""
        (?:
            mfg\.?\s*date|
            mfd|
            manufacturing\s*date|
            pkd|
            packing\s*date|
            packed|
            date\s+of\s+(?:rpg|repacking|packing)
        )
        \s*
        [:.-]?
        \s*
        (
            (?:
                0?[1-9]|1[0-2]
            )
            [-/.]
            (?:20)?
            [0-9]{2}
        |
            (?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)
            [-\s]?
            [0-9]{2,4}
        )
        """,
        combined,
        re.I | re.X,
    )


    if date_match:

        fields["manufacturing_date"] = (
            date_match.group(1)
        )


    # --------------------------------------------------------
    # PRODUCT NAME
    # --------------------------------------------------------

    declaration_words = (
        "mrp",
        "net quantity",
        "net qty",
        "manufactured",
        "mfg",
        "packed",
        "repacked",
        "consumer care",
        "customer care",
        "ingredients",
        "directions",
        "warning",
        "batch",
        "expiry",
        "use before",
        "marketed by",
        "regd office",
        "licence",
        "lic no",
    )


    for text in texts:

        lower = text.lower()

        if (
            len(text) >= 5
            and not any(
                word in lower
                for word in declaration_words
            )
        ):

            fields["product_name"] = text

            break


    return fields


# ============================================================
# DATE VALIDATION
# ============================================================

def _validate_ai_date(
    ai_result,
    ocr_items
):
    """
    Strong anti-hallucination protection.

    The manufacturing date must appear exactly in OCR
    evidence when OCR is available.

    If OCR is disabled, Gemini's strict visual prompt
    remains the primary protection.
    """

    if not ai_result:
        return ai_result


    result = dict(
        ai_result
    )


    date_result = result.get(
        "manufacturing_date"
    )


    if not isinstance(
        date_result,
        dict
    ):

        return result


    value = str(
        date_result.get(
            "value",
            ""
        )
    ).strip()


    if not value:
        return result


    # If OCR is enabled, require exact OCR evidence.
    if ocr_items:

        ocr_text = " ".join(
            str(
                item.get(
                    "text",
                    ""
                )
            ).strip()

            for item in ocr_items

            if isinstance(
                item,
                dict
            )
        )


        # Direct exact match.
        if value in ocr_text:
            return result


        # Some OCR may insert/remove spaces around dates.
        normalized_value = re.sub(
            r"\s+",
            "",
            value.lower()
        )


        normalized_ocr = re.sub(
            r"\s+",
            "",
            ocr_text.lower()
        )


        if normalized_value in normalized_ocr:
            return result


        print(
            "DATE VALIDATION: "
            "rejected unsupported AI date:",
            value
        )


        result[
            "manufacturing_date"
        ] = {
            "value": "",
            "confidence": 0.0,
            "evidence": (
                "Date was not found "
                "exactly in OCR evidence; "
                "needs review."
            ),
        }


    return result


# ============================================================
# MERGE GEMINI + OCR
# ============================================================

def _merge_ai_with_ocr(
    ai_result,
    ocr_items
):

    if not ai_result:
        return None


    fields = {
        key: (
            value or {}
        ).get(
            "value",
            ""
        )

        if isinstance(
            value,
            dict
        )

        else ""

        for key, value
        in ai_result.items()
    }


    # Only use OCR fallback when Gemini missed a field.
    fallback = basic_extraction(
        ocr_items
    )


    for key, value in fallback.items():

        if (
            not fields.get(key)
            and value
        ):

            fields[key] = value


    return fields


# ============================================================
# ANALYZE ENDPOINT
# ============================================================

@app.post("/analyze")
async def analyze(
    files: list[UploadFile] = File(...)
):

    if not files:

        return {
            "success": False,
            "message": (
                "At least one image "
                "is required."
            )
        }


    # ========================================================
    # READ UPLOADS
    # ========================================================

    raw_images = []


    for file in files:

        data = await file.read()


        if not data:
            continue


        mime = (
            file.content_type
            or "image/jpeg"
        )


        if not mime.startswith(
            "image/"
        ):

            continue


        raw_images.append(
            (
                data,
                mime
            )
        )


    if not raw_images:

        return {
            "success": False,
            "message": (
                "No valid image files "
                "were received."
            )
        }


    print(
        "\n================================"
    )

    print(
        "       DRISHTI FAST ANALYSIS"
    )

    print(
        "================================"
    )

    print(
        "Received images:",
        len(raw_images)
    )

    print(
        "Gemini model:",
        GEMINI_MODEL
    )

    print(
        "OCR enabled:",
        USE_OCR
    )


    # ========================================================
    # PREPARE GEMINI IMAGES
    # ========================================================

    gemini_images = []


    for raw, mime in raw_images:

        prepared = (
            build_gemini_image_set(
                raw,
                mime
            )
        )

        gemini_images.extend(
            prepared
        )


    print(
        "Images sent to Gemini:",
        len(gemini_images)
    )


    # ========================================================
    # OPTIONAL OCR
    # ========================================================

    ocr_data = []


    if USE_OCR:

        print(
            "\n--- OCR FALLBACK ---"
        )

        try:

            ocr_data = (
                extract_with_ocr(
                    raw_images
                )
            )

            print(
                "OCR items:",
                len(ocr_data)
            )

        except Exception as exc:

            print(
                "OCR ERROR:",
                repr(exc)
            )

            traceback.print_exc()

            ocr_data = []

    else:

        print(
            "OCR skipped for FAST mode."
        )


    # ========================================================
    # OCR HINT
    # ========================================================

    ocr_hint = (
        format_ocr_hint(
            ocr_data,
            max_items=60
        )
        if ocr_data
        else
        "(OCR disabled in FAST mode)"
    )


    # ========================================================
    # ONE GEMINI CALL
    # ========================================================

    print(
        "\n--- GEMINI VISION EXTRACTION ---"
    )


    ai_result = gemini_extract(
        gemini_images,
        ocr_hint
    )


    # ========================================================
    # DATE VALIDATION
    # ========================================================

    ai_result = (
        _validate_ai_date(
            ai_result,
            ocr_data
        )
        if ai_result
        else None
    )


    # ========================================================
    # SELECT FINAL FIELDS
    # ========================================================

    if ai_result:

        if ocr_data:

            fields = (
                _merge_ai_with_ocr(
                    ai_result,
                    ocr_data
                )
            )

            extraction_source = (
                "GEMINI VISION + OCR FALLBACK"
            )

        else:

            fields = {
                key: (
                    value or {}
                ).get(
                    "value",
                    ""
                )

                if isinstance(
                    value,
                    dict
                )

                else ""

                for key, value
                in ai_result.items()
            }

            extraction_source = (
                "GEMINI VISION FAST MODE"
            )

    else:

        print(
            "Gemini failed."
        )


        if USE_OCR and ocr_data:

            fields = (
                basic_extraction(
                    ocr_data
                )
            )

            extraction_source = (
                "OCR FALLBACK"
            )

        else:

            fields = {
                "product_name": "",
                "mrp": "",
                "net_quantity": "",
                "manufacturer": "",
                "consumer_care": "",
                "manufacturing_date": "",
            }

            extraction_source = (
                "NO EXTRACTION"
            )


    # ========================================================
    # COMPLIANCE
    # ========================================================

    print(
        "\n--- COMPLIANCE ENGINE ---"
    )


    compliance = check_compliance(
    fields,
    ocr_data
)


    # ========================================================
    # FINAL LOG
    # ========================================================

    print(
        "\n========== FINAL FIELDS =========="
    )


    print(
        json.dumps(
            fields,
            indent=2,
            ensure_ascii=False
        )
    )


    print(
        "=================================="
    )


    print(
        "Overall status:",
        compliance.get(
            "overall_status"
        )
    )


    print(
        "================================\n"
    )


    # ========================================================
    # RESPONSE
    # ========================================================

    return {

        "success": True,

        "extraction_source":
            extraction_source,

        "extracted":
            fields,

        "ai_details":
            ai_result,

        # Kept for frontend/debugging compatibility.
        "inventory":
            None,

        "ocr":
            ocr_data,

        "compliance":
            compliance,
    }