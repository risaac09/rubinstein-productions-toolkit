#!/usr/bin/env python3
"""
Build: Generic Capabilities Statement PDF (untailored master template).

Source: 00 Canonical/Independent Evaluator - Positioning.md Section 6.
Markdown companion: 03 Projects/Evaluation Consultancy/01 Content/capabilities-statements/generic-2026-04-30.md
"""
from reportlab.lib.pagesizes import letter
from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.lib.utils import simpleSplit
import os

# Brand palette (locked — matches existing scripts)
INK       = HexColor("#0F1729")
GRAPHITE  = HexColor("#2A2D34")
SLATE     = HexColor("#5A6170")
RULE      = HexColor("#D8D2C3")
OCHRE     = HexColor("#9A6B2F")
DATA      = HexColor("#2B6A6E")
DATA_WASH = HexColor("#E6F3F4")

OUT_DIR = os.path.expanduser(
    "~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second Brain/"
    "03 Projects/Evaluation Consultancy/01 Content/capabilities-statements"
)
os.makedirs(OUT_DIR, exist_ok=True)

OUTPUT_FILE = "Isaac-Rubinstein-Capabilities-Statement-Generic-2026-04-30.pdf"


def build():
    outfile = os.path.join(OUT_DIR, OUTPUT_FILE)
    W, H = letter
    c = pdfcanvas.Canvas(outfile, pagesize=letter)

    # Top INK accent bar
    c.setFillColor(INK)
    c.rect(0, H - 8, W, 8, fill=1, stroke=0)
    c.setStrokeColor(OCHRE)
    c.setLineWidth(1.5)
    c.line(54, H - 12, W - 54, H - 12)

    y = H - 48

    # Name
    c.setFont("Helvetica-Bold", 18)
    c.setFillColor(INK)
    c.drawString(54, y, "ISAAC RUBINSTEIN, MPH")
    y -= 16
    c.setFont("Helvetica", 9.5)
    c.setFillColor(SLATE)
    c.drawString(54, y, "Independent Program Evaluator  |  Health Equity  |  Systems Change  |  Participatory Methods")
    y -= 13
    c.drawString(54, y, "isaac@isaacrubinstein.com  |  isaacrubinstein.com  |  Providence, RI (relocating Oslo, Norway Q3 2026)  |  Remote & hybrid engagements")

    y -= 24
    c.setStrokeColor(OCHRE)
    c.setLineWidth(1.5)
    c.line(54, y, W - 54, y)
    y -= 18

    # Practice description (canonical Section 6)
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(INK)
    c.drawString(54, y, "PRACTICE DESCRIPTION")
    y -= 14

    c.setFont("Helvetica", 9)
    c.setFillColor(GRAPHITE)
    positioning = (
        "Isaac Rubinstein, MPH, is an independent program evaluator specializing in health equity programs, "
        "community health systems, and participatory evaluation methods. He brings MPH-level methodology training "
        "(University of Washington, HSERV 522), institutional measurement and change-management experience from "
        "Seattle Children's Hospital, and a facilitation background that produces stakeholder engagement most "
        "evaluations never achieve. He serves nonprofits with federal grants, health systems, foundations, and "
        "established evaluation firms needing specialist subcontractor support across data collection, qualitative "
        "analysis, evaluation design, and reporting."
    )
    for line in simpleSplit(positioning, "Helvetica", 9, W - 108):
        c.drawString(54, y, line)
        y -= 12

    y -= 8

    # Stats row
    stats = [
        ("60+", "organizations in shared\nmeasurement system"),
        ("14", "population-level indicators\nacross 6 equity domains"),
        ("59%", "reduction in mental health\nservice turnaround"),
        ("$510K", "small business lending\nprogram evaluated"),
    ]
    stat_x = 54
    stat_w = (W - 108) / 4

    c.setFillColor(DATA_WASH)
    c.roundRect(54, y - 36, W - 108, 40, 4, fill=1, stroke=0)

    for i, (num, label) in enumerate(stats):
        sx = stat_x + i * stat_w
        c.setFont("Helvetica-Bold", 18)
        c.setFillColor(DATA)
        c.drawString(sx + 8, y - 2, num)
        c.setFont("Helvetica", 7)
        c.setFillColor(SLATE)
        for j, ll in enumerate(label.split("\n")):
            c.drawString(sx + 8, y - 16 - j * 9, ll)

    y -= 52

    # Two-column layout
    col_w = (W - 108 - 20) / 2
    left_x = 54
    right_x = 54 + col_w + 20

    def draw_section(x, ypos, title, items, col_width):
        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(OCHRE)
        c.drawString(x, ypos, title)
        ypos -= 4
        c.setStrokeColor(RULE)
        c.setLineWidth(0.5)
        c.line(x, ypos, x + col_width, ypos)
        ypos -= 12
        c.setFont("Helvetica", 8)
        c.setFillColor(GRAPHITE)
        for item in items:
            if item.startswith("**"):
                item_clean = item.replace("**", "")
                c.setFont("Helvetica-Bold", 8)
                c.drawString(x, ypos, item_clean)
                c.setFont("Helvetica", 8)
                ypos -= 11
            else:
                lines = simpleSplit(item, "Helvetica", 8, col_width - 8)
                c.drawString(x, ypos, "•  " + lines[0])
                ypos -= 10
                for line in lines[1:]:
                    c.drawString(x + 10, ypos, line)
                    ypos -= 10
        return ypos

    # LEFT COLUMN
    ly = y
    ly = draw_section(left_x, ly, "EVALUATION SERVICES", [
        "**Program Evaluation Design & Implementation",
        "Logic models, theories of change, evaluation plans",
        "Mixed-methods data collection & analysis",
        "Annual and multi-year evaluation reporting",
        "**Shared Measurement & Dashboard Development",
        "Population-level indicator systems",
        "Cross-organization data infrastructure",
        "Interactive dashboards & data visualization",
        "**Participatory & Community-Centered Evaluation",
        "Stakeholder engagement in evaluation design",
        "Community data literacy workshops",
        "Data sovereignty protocol development",
        "**Evaluation Capacity Building & Subcontracting",
        "Technical assistance on data collection & use",
        "Survey design & instrument development",
        "Subcontracting for established eval firms",
    ], col_w)

    ly -= 10
    ly = draw_section(left_x, ly, "METHODS & FRAMEWORKS", [
        "Results-Based Accountability (RBA)",
        "Water of Systems Change (Kania, Kramer, Senge)",
        "Racial Equity Impact Assessment",
        "Mixed methods (surveys, interviews, focus groups, case studies)",
        "Participatory & Developmental Evaluation",
        "Community-Based Participatory Research (CBPR)",
        "MEAL frameworks · Collective Impact shared measurement",
    ], col_w)

    # RIGHT COLUMN
    ry = y
    ry = draw_section(right_x, ry, "SECTOR EXPERIENCE", [
        "**Health Equity Zones / Collective Impact",
        "Central Providence Unidos (RI) — Associate Director of Evaluation & Learning. "
        "Designed 14-indicator shared measurement dashboard across 6 equity domains. Built "
        "evaluation framework integrating RBA, systems change, and racial equity assessment. "
        "Co-designed Data Academy with Brown University. Managed evaluation across 60+ partner organizations.",
        "**Pediatric Healthcare Systems",
        "Seattle Children's Hospital — Reduced mental health service turnaround by 59% through "
        "data-driven process evaluation. Led enterprise-scale Workday change-management readiness "
        "across 3,000+ staff with measurement systems for adoption and organizational readiness.",
    ], col_w)

    ry -= 10
    ry = draw_section(right_x, ry, "FUNDERS & PARTNERS", [
        "CDC Foundation · Blue Meridian Partners · RIDOH · Race Forward · United Way of RI · "
        "Reinvestment Fund · Commerce RI · Brown University Swearer Center · Seattle Children's Hospital",
    ], col_w)

    ry -= 10
    ry = draw_section(right_x, ry, "EDUCATION & CREDENTIALS", [
        "**Master of Public Health (MPH)",
        "University of Washington, School of Public Health (2025)",
        "HSERV 522 (Program Evaluation Methods); Biostatistics, Epidemiology, Health Systems, Qualitative Methods",
        "**Affiliations",
        "American Evaluation Association · AEA Independent Consulting TIG",
    ], col_w)

    # Bottom bar
    bot_y = 42
    c.setStrokeColor(OCHRE)
    c.setLineWidth(0.75)
    c.line(54, bot_y, W - 54, bot_y)
    c.setFont("Helvetica", 7.5)
    c.setFillColor(SLATE)
    c.drawCentredString(W / 2, bot_y - 12,
        "External evaluation  |  Subcontracting  |  Evaluation plan development  |  "
        "Evaluation TA & capacity building  |  Logic model workshops  |  Data analysis & reporting")
    c.drawCentredString(W / 2, bot_y - 22,
        "Sole proprietorship · EIN in process · SAM.gov registration in progress")

    c.save()
    print(f"Capabilities statement: {outfile}")


if __name__ == "__main__":
    build()
