import hashlib
import re

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.services.demo_company_generator.branding import (
    APEX_COLORS,
    draw_apex_wordmark,
)
from app.services.demo_company_generator.models import DEMO_NOTICE
from app.services.demo_company_generator.models import DemoDocumentResult


def money(value):
    if value is None:
        return ""

    return f"${int(value):,}"


def format_date(value):
    return value.strftime("%m/%d/%Y")


def write_pdf(path, title, story, company, *, pages=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(path),
        pagesize=LETTER,
        rightMargin=0.65 * inch,
        leftMargin=0.65 * inch,
        topMargin=0.85 * inch,
        bottomMargin=0.7 * inch,
        title=title,
    )

    if pages:
        final_story = []
        for index, page_story in enumerate(pages):
            if index:
                final_story.append(PageBreak())
            final_story.extend(page_story)
    else:
        final_story = story

    doc.build(
        final_story,
        onFirstPage=lambda canvas, document: _draw_page(
            canvas,
            document,
            title,
            company,
        ),
        onLaterPages=lambda canvas, document: _draw_page(
            canvas,
            document,
            title,
            company,
        ),
    )

    return path


def styles():
    base = getSampleStyleSheet()
    base.add(
        ParagraphStyle(
            name="DemoTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=18,
            leading=22,
            alignment=TA_LEFT,
            textColor=APEX_COLORS["ink"],
            spaceAfter=12,
        )
    )
    base.add(
        ParagraphStyle(
            name="Section",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=15,
            textColor=APEX_COLORS["ink"],
            spaceBefore=10,
            spaceAfter=6,
        )
    )
    base.add(
        ParagraphStyle(
            name="SmallDemo",
            parent=base["Normal"],
            fontSize=8,
            leading=10,
            textColor=APEX_COLORS["muted"],
        )
    )
    base.add(
        ParagraphStyle(
            name="CenterNotice",
            parent=base["Normal"],
            fontSize=8,
            leading=10,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#9a3412"),
        )
    )
    return base


def title(text):
    return Paragraph(text, styles()["DemoTitle"])


def section(text):
    return Paragraph(text, styles()["Section"])


def body(text):
    return Paragraph(text, styles()["Normal"])


def notice():
    return Paragraph(DEMO_NOTICE, styles()["CenterNotice"])


def gap(height=0.12):
    return Spacer(1, height * inch)


def key_value_table(rows, widths=(2.1 * inch, 4.3 * inch)):
    data = [
        [
            Paragraph(f"<b>{label}</b>", styles()["Normal"]),
            Paragraph(str(value), styles()["Normal"]),
        ]
        for label, value in rows
    ]
    table = Table(data, colWidths=list(widths), hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), APEX_COLORS["concrete"]),
                ("TEXTCOLOR", (0, 0), (-1, -1), APEX_COLORS["ink"]),
                ("GRID", (0, 0), (-1, -1), 0.4, APEX_COLORS["line"]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return table


def simple_table(headers, rows):
    data = [
        [
            Paragraph(f"<b>{cell}</b>", styles()["Normal"])
            for cell in headers
        ]
    ]
    data.extend(
        [
            [
                Paragraph(str(cell), styles()["Normal"])
                for cell in row
            ]
            for row in rows
        ]
    )
    table = Table(data, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), APEX_COLORS["ink"]),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.4, APEX_COLORS["line"]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def paragraph_list(items):
    story = []
    for item in items:
        story.append(
            Paragraph(f"- {item}", styles()["Normal"])
        )
    return story


def pdf_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        while True:
            chunk = file.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def pdf_page_count(path):
    data = Path(path).read_bytes()
    return len(
        re.findall(
            rb"/Type\s*/Page\b",
            data,
        )
    )


def document_result(path, document_type, expected_fields=None):
    path = Path(path)
    return DemoDocumentResult(
        filename=path.name,
        document_type=document_type,
        path=path,
        sha256=pdf_sha256(path),
        page_count=pdf_page_count(path),
        expected_extracted_fields=expected_fields or {},
    )


def _draw_page(canvas, document, doc_title, company):
    canvas.saveState()
    width, height = LETTER
    draw_apex_wordmark(canvas, document.leftMargin, height - 0.35 * inch)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(APEX_COLORS["muted"])
    canvas.drawRightString(
        width - document.rightMargin,
        height - 0.3 * inch,
        doc_title,
    )
    canvas.setFillColor(colors.HexColor("#9a3412"))
    canvas.drawCentredString(
        width / 2,
        0.42 * inch,
        DEMO_NOTICE,
    )
    canvas.setFillColor(APEX_COLORS["muted"])
    canvas.drawRightString(
        width - document.rightMargin,
        0.28 * inch,
        f"Page {document.page}",
    )
    canvas.drawString(
        document.leftMargin,
        0.28 * inch,
        f"{company.legal_name} | {company.email}",
    )
    canvas.restoreState()
