#!/usr/bin/env python3
"""
Build: (1) Work Samples Appendix, (2) Updated Capabilities Statement PDF
"""
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether
)
from reportlab.pdfgen import canvas as pdfcanvas
import os

# ── BRAND COLORS ──
INK       = HexColor("#0F1729")
GRAPHITE  = HexColor("#2A2D34")
SLATE     = HexColor("#5A6170")
RULE      = HexColor("#D8D2C3")
CREAM     = HexColor("#F7F4EC")
PAPER     = HexColor("#FFFFFF")
OCHRE     = HexColor("#9A6B2F")
OCHRE_LT  = HexColor("#C8935A")
OCHRE_WASH= HexColor("#F5EDE0")
DATA      = HexColor("#2B6A6E")
DATA_LT   = HexColor("#4A9EA3")
DATA_WASH = HexColor("#E6F3F4")

OUT_DIR = "/sessions/inspiring-focused-maxwell/mnt/01 Evaluation Consultancy/01 Content"

# ════════════════════════════════════════════════════
# PART 1: WORK SAMPLES APPENDIX
# ════════════════════════════════════════════════════

def build_appendix():
    outfile = os.path.join(OUT_DIR, "Isaac-Rubinstein-Work-Samples.pdf")
    
    S = {}
    S['title'] = ParagraphStyle('Title', fontName='Helvetica-Bold', fontSize=22, leading=28,
        textColor=INK, spaceAfter=4, letterSpacing=-0.4)
    S['subtitle'] = ParagraphStyle('Subtitle', fontName='Helvetica', fontSize=11, leading=16,
        textColor=SLATE, spaceAfter=16)
    S['h2'] = ParagraphStyle('H2', fontName='Helvetica-Bold', fontSize=13, leading=18,
        textColor=OCHRE, spaceBefore=16, spaceAfter=6)
    S['body'] = ParagraphStyle('Body', fontName='Helvetica', fontSize=10, leading=15,
        textColor=GRAPHITE, spaceAfter=6, alignment=TA_JUSTIFY)
    S['meta'] = ParagraphStyle('Meta', fontName='Helvetica', fontSize=9, leading=13,
        textColor=SLATE, spaceAfter=3)
    S['link'] = ParagraphStyle('Link', fontName='Helvetica-Bold', fontSize=9.5, leading=14,
        textColor=DATA, spaceAfter=2)
    S['tag'] = ParagraphStyle('Tag', fontName='Helvetica-Bold', fontSize=8, leading=11,
        textColor=PAPER)
    S['footer'] = ParagraphStyle('Footer', fontName='Helvetica', fontSize=8, leading=10,
        textColor=SLATE, alignment=TA_CENTER)

    def first_page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(INK)
        canvas.rect(0, letter[1] - 8, letter[0], 8, fill=1, stroke=0)
        canvas.setStrokeColor(OCHRE)
        canvas.setLineWidth(1.5)
        canvas.line(72, letter[1] - 12, letter[0] - 72, letter[1] - 12)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(SLATE)
        canvas.drawCentredString(letter[0]/2, 36,
            "Isaac Rubinstein  ·  Independent Program Evaluator  ·  isaac@isaacrubinstein.com  ·  Providence, RI")
        canvas.restoreState()

    doc = SimpleDocTemplate(outfile, pagesize=letter,
        topMargin=0.85*inch, bottomMargin=0.75*inch,
        leftMargin=1*inch, rightMargin=1*inch)
    
    story = []
    story.append(Spacer(1, 16))
    story.append(Paragraph("Work Samples", S['title']))
    story.append(Paragraph(
        "Selected evaluation deliverables demonstrating methods, reporting quality, and data infrastructure design.",
        S['subtitle']))
    story.append(HRFlowable(width="100%", thickness=2, color=OCHRE, spaceAfter=14, spaceBefore=2))

    # ── SAMPLE 1: CPU Evaluation Plan ──
    story.append(Paragraph("1. Evaluation Plan — Central Providence Unidos Health Equity Zone", S['h2']))
    
    tag_data = [[Paragraph("EVALUATION PLAN", S['tag']), Paragraph("MIXED METHODS", S['tag']),
                 Paragraph("RHODE ISLAND", S['tag'])]]
    tag_table = Table(tag_data, colWidths=[1.1*inch, 1*inch, 0.9*inch])
    tag_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (0,0), DATA),
        ('BACKGROUND', (1,0), (1,0), OCHRE),
        ('BACKGROUND', (2,0), (2,0), SLATE),
        ('TEXTCOLOR', (0,0), (-1,-1), PAPER),
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
        ('ROUNDEDCORNERS', [3,3,3,3]),
    ]))
    story.append(tag_table)
    story.append(Spacer(1, 6))
    
    story.append(Paragraph(
        "Comprehensive evaluation framework for a collective impact initiative with 60+ partner organizations "
        "in Central Providence, RI. Designed a shared measurement system using Results-Based Accountability, "
        "systems change evaluation (Kania, Kramer, Senge), and racial equity assessment. The plan covers "
        "14 population-level indicators across six equity domains (housing, health, education, quality work, "
        "civic power, climate resilience), with mixed-methods data collection including surveys, interviews, "
        "focus groups, document analysis, and community data storytelling.",
        S['body']))
    story.append(Paragraph(
        "Client: One Neighborhood Builders / Central Providence Unidos  |  Funders: RIDOH, CDC Foundation, "
        "Blue Meridian Partners, Race Forward, United Way of RI, Reinvestment Fund, Commerce RI",
        S['meta']))
    story.append(Paragraph("<b>Relevance to RIPCA:</b> Demonstrates evaluation plan design, multi-stakeholder "
        "coordination, indicator selection, and mixed-methods framework — the same deliverable type "
        "required as the first RIPCA evaluation deliverable.", S['body']))
    story.append(Paragraph("Available on request  ·  Sample attached", S['link']))

    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="100%", thickness=0.5, color=RULE, spaceAfter=4, spaceBefore=4))

    # ── SAMPLE 2: CPU Dashboard ──
    story.append(Paragraph("2. Live Data Dashboard — CentralProvidenceUnidos.org", S['h2']))
    
    tag_data2 = [[Paragraph("DATA INFRASTRUCTURE", S['tag']), Paragraph("LIVE DASHBOARD", S['tag']),
                  Paragraph("POPULATION INDICATORS", S['tag'])]]
    tag_table2 = Table(tag_data2, colWidths=[1.3*inch, 1*inch, 1.4*inch])
    tag_table2.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (0,0), DATA),
        ('BACKGROUND', (1,0), (1,0), OCHRE),
        ('BACKGROUND', (2,0), (2,0), SLATE),
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(tag_table2)
    story.append(Spacer(1, 6))
    
    story.append(Paragraph(
        "Publicly accessible data dashboard tracking 14 population-level indicators drawn from authoritative "
        "sources (U.S. Census, RI state agencies, Eviction Lab, HUD). Each indicator includes definition, "
        "data source, methodology notes, and limitations. The dashboard serves as both an accountability tool "
        "for funders and a community resource for resident-led advocacy.",
        S['body']))
    story.append(Paragraph(
        "Client: Central Providence Unidos / One Neighborhood Builders  |  Live at CentralProvidenceUnidos.org",
        S['meta']))
    story.append(Paragraph("<b>Relevance to RIPCA:</b> Demonstrates ability to build measurement infrastructure "
        "that serves multiple audiences — funders, program staff, and community stakeholders — with clear "
        "documentation of data sources, definitions, and limitations.", S['body']))
    story.append(Paragraph(
        '<link href="https://CentralProvidenceUnidos.org" color="#2B6A6E">https://CentralProvidenceUnidos.org</link>',
        S['link']))

    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="100%", thickness=0.5, color=RULE, spaceAfter=4, spaceBefore=4))

    # ── SAMPLE 3: NNF After the Vote ──
    story.append(Paragraph("3. Final Report — Nine Neighborhood Fund: After the Vote", S['h2']))
    
    tag_data3 = [[Paragraph("FINAL REPORT", S['tag']), Paragraph("100+ PAGES", S['tag']),
                  Paragraph("PARTICIPATORY BUDGETING", S['tag'])]]
    tag_table3 = Table(tag_data3, colWidths=[0.95*inch, 0.85*inch, 1.5*inch])
    tag_table3.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (0,0), DATA),
        ('BACKGROUND', (1,0), (1,0), OCHRE),
        ('BACKGROUND', (2,0), (2,0), SLATE),
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(tag_table3)
    story.append(Spacer(1, 6))
    
    story.append(Paragraph(
        "Comprehensive evaluation report documenting the implementation and outcomes of Providence's "
        "participatory budgeting initiative — a resident-led process allocating public funds across "
        "nine neighborhoods. The report covers process evaluation, project implementation tracking, "
        "community engagement analysis, and recommendations for program improvement.",
        S['body']))
    story.append(Paragraph(
        "Client: Central Providence Unidos / City of Providence",
        S['meta']))
    story.append(Paragraph("<b>Relevance to RIPCA:</b> Demonstrates ability to produce a polished, "
        "publication-quality final evaluation report — the same deliverable type required as the "
        "capstone RIPCA evaluation deliverable. Shows synthesis of mixed-methods findings into "
        "actionable recommendations for funders and program leadership.", S['body']))
    story.append(Paragraph("Copy available on request", S['link']))

    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="100%", thickness=0.5, color=RULE, spaceAfter=4, spaceBefore=4))

    # ── SAMPLE 4: Seattle Children's ──
    story.append(Paragraph("4. KPI Dashboard — Seattle Children's Hospital", S['h2']))
    
    tag_data4 = [[Paragraph("DATA VISUALIZATION", S['tag']), Paragraph("ENTERPRISE SCALE", S['tag']),
                  Paragraph("HEALTHCARE", S['tag'])]]
    tag_table4 = Table(tag_data4, colWidths=[1.2*inch, 1.1*inch, 0.9*inch])
    tag_table4.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (0,0), DATA),
        ('BACKGROUND', (1,0), (1,0), OCHRE),
        ('BACKGROUND', (2,0), (2,0), SLATE),
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(tag_table4)
    story.append(Spacer(1, 6))
    
    story.append(Paragraph(
        "Key performance indicator slides tracking adoption and implementation metrics for an enterprise "
        "systems transition across a 10,000-person pediatric healthcare institution. Designed to communicate "
        "complex operational data to senior leadership in a format that drives decisions — not just reports status.",
        S['body']))
    story.append(Paragraph(
        "Client: Seattle Children's Hospital (confidential — redacted sample available)",
        S['meta']))
    story.append(Paragraph("<b>Relevance to RIPCA:</b> Demonstrates clear data communication to operational "
        "leadership — the same skill required for RIPCA quarterly briefs read by OHS program managers.", S['body']))
    story.append(Paragraph("Redacted sample available on request", S['link']))

    story.append(Spacer(1, 16))
    story.append(HRFlowable(width="100%", thickness=1, color=OCHRE, spaceAfter=10, spaceBefore=6))
    story.append(Paragraph(
        "Additional samples — including the CPU Action Plan Dashboard with embedded evaluation metrics, "
        "program-specific logic models, and data collection instruments — are available on request.",
        S['meta']))

    doc.build(story, onFirstPage=first_page, onLaterPages=first_page)
    print(f"Work Samples appendix: {outfile}")


# ════════════════════════════════════════════════════
# PART 2: CAPABILITIES STATEMENT PDF (1-page)
# ════════════════════════════════════════════════════

def build_capstmt():
    outfile = os.path.join(OUT_DIR, "Isaac Rubinstein - Evaluation Capabilities Statement.pdf")
    W, H = letter
    c = pdfcanvas.Canvas(outfile, pagesize=letter)
    
    # ── TOP BAR ──
    c.setFillColor(INK)
    c.rect(0, H - 8, W, 8, fill=1, stroke=0)
    c.setStrokeColor(OCHRE)
    c.setLineWidth(1.5)
    c.line(54, H - 12, W - 54, H - 12)
    
    y = H - 48
    
    # ── NAME ──
    c.setFont("Helvetica-Bold", 18)
    c.setFillColor(INK)
    c.drawString(54, y, "ISAAC RUBINSTEIN, MPH")
    y -= 16
    c.setFont("Helvetica", 9.5)
    c.setFillColor(SLATE)
    c.drawString(54, y, "Independent Program Evaluator  |  Health Equity  |  Systems Change  |  Participatory Methods")
    y -= 13
    c.drawString(54, y, "isaac@isaacrubinstein.com  |  Providence, RI (relocating Oslo, Norway September 2026)  |  Available for remote & hybrid engagements")
    
    y -= 24
    c.setStrokeColor(OCHRE)
    c.setLineWidth(1.5)
    c.line(54, y, W - 54, y)
    y -= 18
    
    # ── CORE POSITIONING ──
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(INK)
    c.drawString(54, y, "CORE POSITIONING")
    y -= 14
    
    c.setFont("Helvetica", 9)
    c.setFillColor(GRAPHITE)
    positioning = (
        "I design and lead evaluations of health equity programs, collective impact initiatives, and systems change efforts. My approach "
        "integrates Results-Based Accountability, equity-centered measurement, and participatory methods that ensure communities "
        "closest to the work have agency over how data is collected, interpreted, and used. I bring seven years of healthcare and public "
        "health experience spanning clinical program evaluation, shared measurement system design, and enterprise change management."
    )
    # Simple text wrapping
    from reportlab.lib.utils import simpleSplit
    lines = simpleSplit(positioning, "Helvetica", 9, W - 108)
    for line in lines:
        c.drawString(54, y, line)
        y -= 12
    
    y -= 8
    
    # ── STATS ROW ──
    stats = [
        ("60+", "organizations in shared\nmeasurement system"),
        ("14", "population-level indicators\nacross 6 equity domains"),
        ("59%", "reduction in mental health\nservice turnaround"),
        ("$510K", "small business lending\nprogram evaluated"),
    ]
    stat_x = 54
    stat_w = (W - 108) / 4
    
    # Draw stat boxes
    c.setFillColor(DATA_WASH)
    c.roundRect(54, y - 36, W - 108, 40, 4, fill=1, stroke=0)
    
    for i, (num, label) in enumerate(stats):
        sx = stat_x + i * stat_w
        c.setFont("Helvetica-Bold", 18)
        c.setFillColor(DATA)
        c.drawString(sx + 8, y - 2, num)
        c.setFont("Helvetica", 7)
        c.setFillColor(SLATE)
        label_lines = label.split("\n")
        for j, ll in enumerate(label_lines):
            c.drawString(sx + 8, y - 16 - j*9, ll)
    
    y -= 52
    
    # ── TWO COLUMN LAYOUT ──
    col_w = (W - 108 - 20) / 2  # 20px gutter
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
                # Bold header
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
    
    # LEFT COLUMN: Services + Methods
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
        "Resident/stakeholder engagement in evaluation design",
        "Community data literacy workshops",
        "Data sovereignty protocol development",
        "**Evaluation Capacity Building",
        "Technical assistance on data collection & use",
        "Partner evaluation framework development",
        "Survey design & instrument development",
    ], col_w)
    
    ly -= 10
    ly = draw_section(left_x, ly, "METHODS & FRAMEWORKS", [
        "Results-Based Accountability (RBA)",
        "Water of Systems Change (Kania, Kramer, Senge)",
        "Racial Equity Impact Assessment",
        "Mixed Methods (surveys, interviews, focus groups, case studies)",
        "Participatory & Developmental Evaluation",
        "Community-Based Participatory Research (CBPR)",
        "Collective Impact shared measurement",
    ], col_w)
    
    # RIGHT COLUMN: Experience + Education
    ry = y
    ry = draw_section(right_x, ry, "SECTOR EXPERIENCE", [
        "**Health Equity Zones / Collective Impact",
        "Central Providence Unidos (RI) — Associate Director of Evaluation & Learning. Designed 14-indicator shared measurement dashboard across 6 equity domains. Built evaluation framework integrating RBA, systems change, and racial equity assessment. Co-designed Data Academy with Brown University. Managed evaluation across 60+ partner organizations.",
        "**Pediatric Healthcare Systems",
        "Seattle Children's Hospital — Reduced mental health service turnaround by 59% through data-driven process evaluation. Led enterprise-scale Workday change management across 3,000+ staff with measurement systems for adoption and organizational readiness.",
    ], col_w)
    
    ry -= 10
    ry = draw_section(right_x, ry, "FUNDERS & PARTNERS", [
        "CDC Foundation, Blue Meridian Partners, RIDOH, Race Forward, United Way of RI, Reinvestment Fund, Commerce RI, Brown University Swearer Center, Seattle Children's Hospital",
    ], col_w)
    
    ry -= 10
    ry = draw_section(right_x, ry, "EDUCATION & CREDENTIALS", [
        "**Master of Public Health (MPH)",
        "University of Washington, School of Public Health",
        "Coursework: Program Evaluation (HSERV 522), Biostatistics, Epidemiology, Health Systems, Qualitative Methods",
        "**Affiliations",
        "American Evaluation Association, AEA Independent Consulting TIG",
    ], col_w)
    
    # ── BOTTOM BAR ──
    bot_y = 42
    c.setStrokeColor(OCHRE)
    c.setLineWidth(0.75)
    c.line(54, bot_y, W - 54, bot_y)
    c.setFont("Helvetica", 7.5)
    c.setFillColor(SLATE)
    c.drawCentredString(W/2, bot_y - 12,
        "Available for: External program evaluation  |  Evaluation subcontracting  |  Evaluation plan development  |  "
        "Evaluation TA & capacity building  |  Logic model workshops  |  Data analysis & reporting")
    
    c.save()
    print(f"Capabilities statement: {outfile}")


# ── RUN BOTH ──
build_appendix()
build_capstmt()
