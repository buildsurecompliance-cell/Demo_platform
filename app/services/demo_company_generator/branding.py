from reportlab.lib import colors


APEX_COLORS = {
    "ink": colors.HexColor("#1f2933"),
    "steel": colors.HexColor("#455a64"),
    "concrete": colors.HexColor("#eef1f3"),
    "safety": colors.HexColor("#f4b000"),
    "line": colors.HexColor("#c9d1d9"),
    "muted": colors.HexColor("#667085"),
}


def draw_apex_wordmark(canvas, x, y, width=150):
    canvas.saveState()
    canvas.setFillColor(APEX_COLORS["ink"])
    canvas.roundRect(x, y - 22, 42, 32, 4, fill=1, stroke=0)
    canvas.setFillColor(APEX_COLORS["safety"])
    canvas.setFont("Helvetica-Bold", 15)
    canvas.drawCentredString(x + 21, y - 12, "AC")
    canvas.setFillColor(APEX_COLORS["ink"])
    canvas.setFont("Helvetica-Bold", 13)
    canvas.drawString(x + 50, y - 3, "APEX CONCRETE")
    canvas.setFillColor(APEX_COLORS["muted"])
    canvas.setFont("Helvetica", 7)
    canvas.drawString(x + 50, y - 14, "Fictional Demo Subcontractor")
    canvas.setStrokeColor(APEX_COLORS["line"])
    canvas.line(x, y - 28, x + width, y - 28)
    canvas.restoreState()
