#!/usr/bin/env python3
"""
RIPCA FFY2026 Independent Evaluation Proposal
Isaac Rubinstein — Professional PDF
"""
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, HRFlowable, KeepTogether
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
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
SUCCESS   = HexColor("#3A7D44")
CAUTION   = HexColor("#B5760A")
ALERT     = HexColor("#A03030")

# ── OUTPUT PATH ──
OUTPUT_DIR = "/sessions/inspiring-focused-maxwell/mnt/01 Evaluation Consultancy/02 Prospects/RIPCA-HS1-2026"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "Rubinstein-RIPCA-FFY2026-Evaluation-Proposal.pdf")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── STYLES ──
def make_styles():
    s = {}
    s['title'] = ParagraphStyle(
        'Title', fontName='Helvetica-Bold', fontSize=26, leading=32,
        textColor=INK, spaceAfter=6, alignment=TA_LEFT,
        letterSpacing=-0.5
    )
    s['subtitle'] = ParagraphStyle(
        'Subtitle', fontName='Helvetica', fontSize=13, leading=18,
        textColor=SLATE, spaceAfter=24, alignment=TA_LEFT
    )
    s['h1'] = ParagraphStyle(
        'H1', fontName='Helvetica-Bold', fontSize=18, leading=24,
        textColor=INK, spaceBefore=28, spaceAfter=10,
        borderWidth=0
    )
    s['h2'] = ParagraphStyle(
        'H2', fontName='Helvetica-Bold', fontSize=14, leading=19,
        textColor=OCHRE, spaceBefore=18, spaceAfter=8
    )
    s['h3'] = ParagraphStyle(
        'H3', fontName='Helvetica-Bold', fontSize=11, leading=15,
        textColor=DATA, spaceBefore=12, spaceAfter=6
    )
    s['body'] = ParagraphStyle(
        'Body', fontName='Helvetica', fontSize=10.5, leading=16,
        textColor=GRAPHITE, spaceAfter=8, alignment=TA_JUSTIFY
    )
    s['body_tight'] = ParagraphStyle(
        'BodyTight', fontName='Helvetica', fontSize=10, leading=14.5,
        textColor=GRAPHITE, spaceAfter=4, alignment=TA_JUSTIFY
    )
    s['bullet'] = ParagraphStyle(
        'Bullet', fontName='Helvetica', fontSize=10.5, leading=15,
        textColor=GRAPHITE, spaceAfter=4, leftIndent=18, bulletIndent=6,
        alignment=TA_LEFT
    )
    s['meta'] = ParagraphStyle(
        'Meta', fontName='Helvetica', fontSize=9.5, leading=14,
        textColor=SLATE, spaceAfter=4
    )
    s['callout'] = ParagraphStyle(
        'Callout', fontName='Helvetica-Oblique', fontSize=10.5, leading=15,
        textColor=DATA, spaceAfter=8, leftIndent=12, rightIndent=12
    )
    s['table_header'] = ParagraphStyle(
        'TableHeader', fontName='Helvetica-Bold', fontSize=9.5, leading=13,
        textColor=PAPER, alignment=TA_LEFT
    )
    s['table_cell'] = ParagraphStyle(
        'TableCell', fontName='Helvetica', fontSize=9.5, leading=13,
        textColor=GRAPHITE, alignment=TA_LEFT
    )
    s['table_cell_right'] = ParagraphStyle(
        'TableCellRight', fontName='Helvetica', fontSize=9.5, leading=13,
        textColor=GRAPHITE, alignment=TA_RIGHT
    )
    s['table_cell_bold'] = ParagraphStyle(
        'TableCellBold', fontName='Helvetica-Bold', fontSize=9.5, leading=13,
        textColor=GRAPHITE, alignment=TA_LEFT
    )
    s['footer'] = ParagraphStyle(
        'Footer', fontName='Helvetica', fontSize=8, leading=10,
        textColor=SLATE, alignment=TA_CENTER
    )
    s['page_header'] = ParagraphStyle(
        'PageHeader', fontName='Helvetica', fontSize=8, leading=10,
        textColor=SLATE, alignment=TA_RIGHT
    )
    s['small'] = ParagraphStyle(
        'Small', fontName='Helvetica', fontSize=9, leading=13,
        textColor=SLATE, spaceAfter=4
    )
    return s

S = make_styles()

# ── HELPER FUNCTIONS ──
def ochre_rule():
    return HRFlowable(width="100%", thickness=2, color=OCHRE, spaceAfter=12, spaceBefore=4)

def thin_rule():
    return HRFlowable(width="100%", thickness=0.5, color=RULE, spaceAfter=8, spaceBefore=4)

def bullet(text):
    return Paragraph(f"<bullet>&bull;</bullet>{text}", S['bullet'])

def section_header(text):
    return Paragraph(text, S['h1'])

def sub_header(text):
    return Paragraph(text, S['h2'])

def body(text):
    return Paragraph(text, S['body'])

def body_tight(text):
    return Paragraph(text, S['body_tight'])

def callout(text):
    return Paragraph(text, S['callout'])

# ── PAGE TEMPLATES ──
def first_page(canvas, doc):
    canvas.saveState()
    # Top accent bar
    canvas.setFillColor(INK)
    canvas.rect(0, letter[1] - 8, letter[0], 8, fill=1, stroke=0)
    # Ochre thin accent line below
    canvas.setStrokeColor(OCHRE)
    canvas.setLineWidth(1.5)
    canvas.line(72, letter[1] - 12, letter[0] - 72, letter[1] - 12)
    # Footer
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(SLATE)
    canvas.drawCentredString(letter[0]/2, 36, "Isaac Rubinstein  ·  Independent Program Evaluator  ·  isaac@isaacrubinstein.com  ·  Rhode Island")
    canvas.restoreState()

def later_pages(canvas, doc):
    canvas.saveState()
    # Top rule
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.5)
    canvas.line(72, letter[1] - 36, letter[0] - 72, letter[1] - 36)
    # Header
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(SLATE)
    canvas.drawRightString(letter[0] - 72, letter[1] - 30, "RIPCA FFY2026 Evaluation Proposal  ·  Isaac Rubinstein")
    # Footer
    canvas.drawCentredString(letter[0]/2, 36, f"Page {doc.page}")
    # Bottom accent
    canvas.setStrokeColor(OCHRE)
    canvas.setLineWidth(0.75)
    canvas.line(72, 48, letter[0] - 72, 48)
    canvas.restoreState()

# ── BUILD DOCUMENT ──
doc = SimpleDocTemplate(
    OUTPUT_FILE,
    pagesize=letter,
    topMargin=0.9*inch,
    bottomMargin=0.85*inch,
    leftMargin=1*inch,
    rightMargin=1*inch
)

story = []

# ════════════════════════════════════════════════
# PAGE 1: COVER / INTRO
# ════════════════════════════════════════════════

story.append(Spacer(1, 24))
story.append(Paragraph("Proposal for Independent<br/>Program Evaluation", S['title']))
story.append(Spacer(1, 4))
story.append(Paragraph(
    "Rhode Island Police Chiefs Association  ·  FFY2026 Highway Safety Grant Activities",
    S['subtitle']
))
story.append(ochre_rule())

# Meta block
meta_data = [
    ["Prepared for:", "Chief Sidney Wordell (ret.), Executive Director, RIPCA"],
    ["Prepared by:", "Isaac Rubinstein, MPH  ·  Independent Evaluator"],
    ["Date:", "April 18, 2026"],
    ["Engagement period:", "May 1, 2026 – October 31, 2026"],
    ["Grant cycle:", "FFY2026 (October 1, 2025 – September 30, 2026)"],
]
meta_table = Table(meta_data, colWidths=[1.6*inch, 4.6*inch])
meta_table.setStyle(TableStyle([
    ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'),
    ('FONTNAME', (1,0), (1,-1), 'Helvetica'),
    ('FONTSIZE', (0,0), (-1,-1), 10),
    ('TEXTCOLOR', (0,0), (0,-1), SLATE),
    ('TEXTCOLOR', (1,0), (1,-1), GRAPHITE),
    ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ('TOPPADDING', (0,0), (-1,-1), 3),
    ('BOTTOMPADDING', (0,0), (-1,-1), 3),
    ('LEFTPADDING', (0,0), (-1,-1), 0),
]))
story.append(meta_table)
story.append(Spacer(1, 20))

# Executive Summary
story.append(section_header("Executive Summary"))
story.append(thin_rule())
story.append(body(
    "I am proposing to serve as the independent evaluator for RIPCA's FFY2026 traffic safety grant activities, "
    "funded through the RIDOT Office on Highway Safety. This proposal covers the full scope of evaluation work "
    "described in the RIPCA Program Evaluation Scope of Work — seven evaluation domains spanning media and outreach, "
    "coalition engagement, enforcement resource utilization, judicial collaboration, impaired driving programs, "
    "training and leadership, and strategic partnerships."
))
story.append(body(
    "The engagement runs May 1 through October 31, 2026, at a fixed fee of <b>$25,000</b>. I will deliver an evaluation "
    "plan, quarterly evaluation briefs, a final evaluation report, and a stakeholder presentation. All work follows "
    "a mixed-methods approach aligned with NHTSA performance-based planning expectations."
))
story.append(body(
    "My background is in healthcare systems change, program evaluation, and cross-sector facilitation. I hold an MPH "
    "from the University of Washington with formal evaluation training, and have designed evaluation and shared "
    "measurement systems for publicly funded programs — including a Health Equity Zone spanning 60+ community "
    "partner organizations here in Rhode Island. I have no prior contracts with RIPCA, OHS, or municipal law "
    "enforcement agencies — ensuring full independence."
))

story.append(PageBreak())

# ════════════════════════════════════════════════
# PAGE 2-3: SCOPE OF EVALUATION
# ════════════════════════════════════════════════

story.append(section_header("1. Scope of Evaluation"))
story.append(thin_rule())
story.append(body(
    "The evaluation addresses all seven domains specified in the RIPCA Program Evaluation SOW. "
    "For each domain, I describe the evaluation questions, methods, and data sources I will employ. "
    "Because this engagement begins six months into the FFY2026 grant period, Q1 (Oct–Dec 2025) "
    "and Q2 (Jan–Mar 2026) will be evaluated retroactively through document review and stakeholder "
    "interviews. Q3 (Apr–Jun 2026) and Q4 (Jul–Sep 2026) will be evaluated in real time."
))

# Domain A
story.append(sub_header("A. Media, Communications, and Public Outreach"))
story.append(body(
    "I will assess the effectiveness of RIPCA's media and outreach activities across all emphasis areas. "
    "This includes reviewing the planning and execution of up to four annual enforcement media events, "
    "evaluating press materials and social media toolkits for clarity and consistency with statewide messaging, "
    "and reviewing RIPCA newsletters for frequency, reach, and stakeholder representation."
))
story.append(Paragraph("<b>Key evaluation questions:</b>", S['body']))
story.append(bullet("Were media events executed as planned? How many agencies participated, and what was the geographic distribution?"))
story.append(bullet("Are social media toolkits being used by local law enforcement, and are they consistent with OHS messaging priorities?"))
story.append(bullet("Does the newsletter maintain a regular publication cadence and accurately represent multi-stakeholder activities?"))
story.append(Paragraph("<b>Methods:</b> Document review, content analysis, output tracking (event counts, media impressions, agency participation rates).", S['small']))

# Domain B
story.append(sub_header("B. Coalition Engagement and Reporting"))
story.append(body(
    "I will observe and assess RIPCA's participation in the monthly Traffic Safety Coalition meetings and "
    "review documentation from the Impaired Driving Engagement Council (IDEC). This includes evaluating the "
    "consistency and quality of meeting notes, minutes, and activities reported in monthly submissions, as well "
    "as the timeliness and relevance of legislative updates provided to Chief Abbate."
))
story.append(Paragraph("<b>Methods:</b> Meeting observation, document review, attendance tracking, stakeholder interviews.", S['small']))

# Domain C
story.append(sub_header("C. Enforcement and Education Resource Utilization"))
story.append(body(
    "I will evaluate the planning and coordination of BAT Vehicle deployment for both enforcement and education "
    "purposes. This includes reviewing schedules and usage logs, assessing the number of participating departments, "
    "frequency of use, and geographic coverage, and identifying barriers to utilization."
))
story.append(Paragraph("<b>Methods:</b> Deployment log review, utilization analysis, stakeholder interviews with participating departments.", S['small']))

# Domain D
story.append(sub_header("D. Judicial Collaboration and Data Access"))
story.append(body(
    "I will assess RIPCA's progress in establishing working relationships with the judicial branch, "
    "including efforts toward data-sharing agreements, the availability of judicial data (refusal totals, "
    "violations in emphasis areas), and the feasibility of proposed judicial training methods. Judicial data "
    "access is a known challenge — where data is unavailable, I will document barriers and progress toward access "
    "as evaluation findings in their own right."
))
story.append(Paragraph("<b>Methods:</b> Document review, stakeholder interviews (TSRP, court contacts), progress tracking against data access milestones.", S['small']))

# Domain E
story.append(sub_header("E. Impaired Driving and DRE Program"))
story.append(body(
    "I will evaluate the DRE call-out system's design, implementation status, and stakeholder utilization, "
    "as well as RIPCA's efforts to promote DRE enrollment and certification. I will also assess the oversight "
    "and functioning of the RI Mid-Range DUI Engagement Council, including meeting frequency, stakeholder "
    "participation, and alignment with impaired driving priorities under the mid-range state designation."
))
story.append(Paragraph("<b>Methods:</b> DRE enrollment/certification data review, IDEC meeting observation, stakeholder interviews, progress tracking.", S['small']))

# Domain F
story.append(sub_header("F. Training, Leadership, and Stakeholder Engagement"))
story.append(body(
    "I will assess the development and delivery of in-service training for police chiefs, the Law Enforcement "
    "Chiefs Forum, and cross-sector collaboration efforts including cannabis retail engagement on responsible "
    "consumption messaging. For training events, I will evaluate content relevance, attendance, and perceived "
    "usefulness through participant surveys or interviews."
))
story.append(Paragraph("<b>Methods:</b> Training attendance data, participant surveys/interviews, event observation, document review.", S['small']))

# Domain G
story.append(sub_header("G. Strategic Partnerships and Special Initiatives"))
story.append(body(
    "I will assess progress on collaborative initiatives including the green lab development (AG's Office, DOH, "
    "OHS, URI Crime Lab) and stakeholder efforts to better understand the driving population on Rhode Island "
    "roadways. I will document challenges, innovations, and best practices emerging from cross-agency "
    "collaboration, as well as the development status of the proposed impaired driving arrest database and "
    "traffic stop data analysis portal."
))
story.append(Paragraph("<b>Methods:</b> Stakeholder interviews, document review, progress tracking against stated milestones.", S['small']))

story.append(PageBreak())

# ════════════════════════════════════════════════
# PAGE 4: METHODOLOGY
# ════════════════════════════════════════════════

story.append(section_header("2. Methodology"))
story.append(thin_rule())
story.append(body(
    "This evaluation uses a <b>mixed-methods approach</b> combining quantitative output tracking with qualitative "
    "data collection. The design is primarily a <b>process evaluation</b> — appropriate for a coordination and "
    "capacity-building grant — supplemented by outcome measurement where feasible."
))

# Methods table
methods_header = [
    Paragraph("Method", S['table_header']),
    Paragraph("Application", S['table_header']),
    Paragraph("Domains", S['table_header']),
]
methods_data = [
    methods_header,
    [Paragraph("Document Review", S['table_cell_bold']),
     Paragraph("Meeting minutes, media materials, training records, deployment logs, monthly reports, newsletters", S['table_cell']),
     Paragraph("A–G", S['table_cell'])],
    [Paragraph("Output Tracking", S['table_cell_bold']),
     Paragraph("Event counts, agency participation rates, training attendance, DRE certifications, BAT deployments", S['table_cell']),
     Paragraph("A, C, E, F", S['table_cell'])],
    [Paragraph("Stakeholder Interviews", S['table_cell_bold']),
     Paragraph("Semi-structured interviews with RIPCA staff, OHS program managers, municipal chiefs, IDEC members, TSRP", S['table_cell']),
     Paragraph("A–G", S['table_cell'])],
    [Paragraph("Meeting Observation", S['table_cell_bold']),
     Paragraph("Direct observation of TSC meetings, IDEC meetings, training events, media events", S['table_cell']),
     Paragraph("A, B, E, F", S['table_cell'])],
    [Paragraph("Content Analysis", S['table_cell_bold']),
     Paragraph("Press materials, social media toolkits, newsletters assessed for clarity, consistency, and alignment", S['table_cell']),
     Paragraph("A", S['table_cell'])],
    [Paragraph("Survey/Feedback", S['table_cell_bold']),
     Paragraph("Post-training participant surveys, stakeholder perception surveys (brief, targeted)", S['table_cell']),
     Paragraph("F", S['table_cell'])],
]
methods_table = Table(methods_data, colWidths=[1.5*inch, 3.5*inch, 0.8*inch])
methods_table.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), DATA),
    ('TEXTCOLOR', (0,0), (-1,0), PAPER),
    ('BACKGROUND', (0,1), (-1,-1), PAPER),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [PAPER, DATA_WASH]),
    ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ('TOPPADDING', (0,0), (-1,-1), 6),
    ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ('LEFTPADDING', (0,0), (-1,-1), 8),
    ('RIGHTPADDING', (0,0), (-1,-1), 8),
    ('GRID', (0,0), (-1,-1), 0.5, RULE),
    ('LINEABOVE', (0,0), (-1,0), 1.5, DATA),
]))
story.append(Spacer(1, 8))
story.append(methods_table)
story.append(Spacer(1, 12))

story.append(sub_header("Evaluation Framework"))
story.append(body(
    "The evaluation is organized around a logic model mapping RIPCA's grant-funded activities to outputs "
    "and short-to-intermediate-term outcomes. I will develop this logic model collaboratively with RIPCA "
    "in the first two weeks of the engagement and include it in the Evaluation Plan."
))
story.append(body(
    "Consistent with NHTSA's Countermeasures That Work framework, I will track both <b>process measures</b> "
    "(were activities implemented as planned, with what fidelity and reach?) and <b>outcome measures</b> "
    "(did activities contribute to measurable changes in enforcement capacity, stakeholder coordination, "
    "or public awareness?). Where causal attribution to RIPCA's specific activities is not feasible — "
    "as is the case for crash and fatality reduction — I will state this limitation explicitly and focus "
    "on contribution analysis rather than attribution."
))

story.append(sub_header("Retroactive Evaluation Design"))
story.append(body(
    "Because this engagement begins in May 2026, the first two quarters of FFY2026 (October 2025 – March 2026) "
    "will be evaluated retroactively. This is a common and accepted practice in mid-cycle evaluation start-ups. "
    "The retroactive evaluation will rely on:"
))
story.append(bullet("RIPCA's monthly reports submitted to OHS during Q1 and Q2"))
story.append(bullet("IDEC and Traffic Safety Coalition meeting minutes from October 2025 through March 2026"))
story.append(bullet("Training records, media materials, and deployment logs from the period"))
story.append(bullet("Stakeholder interviews capturing recollections and perspectives on Q1/Q2 activities"))
story.append(body(
    "I will note the retroactive nature of Q1/Q2 findings in all deliverables and weight them accordingly "
    "in the final assessment. Q3 and Q4 will benefit from real-time observation and data collection."
))

story.append(PageBreak())

# ════════════════════════════════════════════════
# PAGE 5: DELIVERABLES & TIMELINE
# ════════════════════════════════════════════════

story.append(section_header("3. Deliverables and Timeline"))
story.append(thin_rule())

timeline_header = [
    Paragraph("Deliverable", S['table_header']),
    Paragraph("Description", S['table_header']),
    Paragraph("Target Date", S['table_header']),
]
timeline_data = [
    timeline_header,
    [Paragraph("Evaluation Plan", S['table_cell_bold']),
     Paragraph("Logic model, evaluation questions, data sources, metrics, methods, data collection matrix, and timeline. Submitted for RIPCA/OHS review.", S['table_cell']),
     Paragraph("May 23, 2026", S['table_cell'])],
    [Paragraph("Q1–Q2 Evaluation Brief", S['table_cell_bold']),
     Paragraph("Combined retroactive brief covering Oct 2025 – Mar 2026. Document review-based. Executive summary + findings by domain + limitations.", S['table_cell']),
     Paragraph("June 20, 2026", S['table_cell'])],
    [Paragraph("Q3 Evaluation Brief", S['table_cell_bold']),
     Paragraph("Real-time evaluation of Apr – Jun 2026 activities. First brief with direct observation data. Progress indicators and emerging findings.", S['table_cell']),
     Paragraph("July 31, 2026", S['table_cell'])],
    [Paragraph("Q4 Evaluation Brief", S['table_cell_bold']),
     Paragraph("Evaluation of Jul – Sep 2026 activities. Includes cumulative progress tracking and preliminary recommendations.", S['table_cell']),
     Paragraph("October 15, 2026", S['table_cell'])],
    [Paragraph("Final Evaluation Report", S['table_cell_bold']),
     Paragraph("Comprehensive assessment: implementation fidelity, output/outcome findings by domain, data gaps, limitations, and actionable recommendations for FFY2027.", S['table_cell']),
     Paragraph("October 31, 2026", S['table_cell'])],
    [Paragraph("Stakeholder Presentation", S['table_cell_bold']),
     Paragraph("Findings presentation to OHS and key stakeholders. Format and scheduling to be coordinated with RIPCA.", S['table_cell']),
     Paragraph("Nov 2026 (TBD)", S['table_cell'])],
]
timeline_table = Table(timeline_data, colWidths=[1.6*inch, 3.2*inch, 1.1*inch])
timeline_table.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), INK),
    ('TEXTCOLOR', (0,0), (-1,0), PAPER),
    ('BACKGROUND', (0,1), (-1,-1), PAPER),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [PAPER, OCHRE_WASH]),
    ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ('TOPPADDING', (0,0), (-1,-1), 7),
    ('BOTTOMPADDING', (0,0), (-1,-1), 7),
    ('LEFTPADDING', (0,0), (-1,-1), 8),
    ('RIGHTPADDING', (0,0), (-1,-1), 8),
    ('GRID', (0,0), (-1,-1), 0.5, RULE),
    ('LINEABOVE', (0,0), (-1,0), 1.5, INK),
]))
story.append(timeline_table)
story.append(Spacer(1, 12))

story.append(sub_header("Quarterly Brief Format"))
story.append(body(
    "Each quarterly brief will follow a consistent format designed for utility:"
))
story.append(bullet("<b>One-page executive summary</b> with key findings and a single decision point for the reader"))
story.append(bullet("<b>Domain-by-domain findings</b> organized around the seven SOW areas, with implementation fidelity indicators (on track / needs attention / off track)"))
story.append(bullet("<b>Data gaps and limitations</b> — what could not be assessed and why"))
story.append(bullet("<b>Emerging recommendations</b> — actionable adjustments RIPCA and OHS can consider before the next quarter"))
story.append(bullet("<b>Data appendix</b> with output tables for anyone who needs the detail"))

story.append(sub_header("Monthly Touchpoints"))
story.append(body(
    "Between deliverables, I will hold a brief monthly check-in with the RIPCA Executive Director (30 minutes) "
    "to review data collection progress, surface emerging issues, and coordinate upcoming observation opportunities. "
    "These touchpoints keep the evaluation responsive without crossing into program delivery."
))

story.append(PageBreak())

# ════════════════════════════════════════════════
# PAGE 6: FEE STRUCTURE & QUALIFICATIONS
# ════════════════════════════════════════════════

story.append(section_header("4. Fee Structure"))
story.append(thin_rule())

fee_header = [
    Paragraph("Component", S['table_header']),
    Paragraph("Detail", S['table_header']),
    Paragraph("Amount", S['table_header']),
]
fee_data = [
    fee_header,
    [Paragraph("Hourly rate", S['table_cell_bold']),
     Paragraph("All evaluation activities: planning, data collection, analysis, reporting, observation, interviews, presentations", S['table_cell']),
     Paragraph("$150/hour", S['table_cell_right'])],
    [Paragraph("Estimated effort", S['table_cell_bold']),
     Paragraph("Approximately 167 total hours across the engagement (~20–28 hours/month depending on phase, with heavier effort during start-up, retroactive review, and final reporting)", S['table_cell']),
     Paragraph("—", S['table_cell_right'])],
    [Paragraph("Engagement period", S['table_cell_bold']),
     Paragraph("May 1, 2026 through delivery of the final report (October 31, 2026) and stakeholder presentation (November 2026)", S['table_cell']),
     Paragraph("—", S['table_cell_right'])],
    [Paragraph("Total fixed fee", S['table_cell_bold']),
     Paragraph("Covers all deliverables specified in this proposal including the evaluation plan, quarterly briefs, final report, and stakeholder presentation", S['table_cell']),
     Paragraph("<b>$25,000</b>", S['table_cell_right'])],
]
fee_table = Table(fee_data, colWidths=[1.5*inch, 3.3*inch, 1.1*inch])
fee_table.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), OCHRE),
    ('TEXTCOLOR', (0,0), (-1,0), PAPER),
    ('BACKGROUND', (0,1), (-1,-1), PAPER),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [PAPER, OCHRE_WASH]),
    ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ('TOPPADDING', (0,0), (-1,-1), 7),
    ('BOTTOMPADDING', (0,0), (-1,-1), 7),
    ('LEFTPADDING', (0,0), (-1,-1), 8),
    ('RIGHTPADDING', (0,0), (-1,-1), 8),
    ('GRID', (0,0), (-1,-1), 0.5, RULE),
    ('LINEABOVE', (0,0), (-1,0), 1.5, OCHRE),
    ('LINEBELOW', (0,-1), (-1,-1), 1.5, OCHRE),
]))
story.append(fee_table)
story.append(Spacer(1, 8))

story.append(body(
    "Invoices will be submitted monthly with a 15-day payment window. Each invoice will include a brief "
    "summary of hours worked and activities completed during the billing period. Travel within Rhode Island "
    "for meeting observation and site visits is included in the fee. No additional costs for materials, "
    "technology, or report production."
))

story.append(Spacer(1, 10))
story.append(sub_header("Monthly Effort Allocation"))
story.append(body(
    "The table below shows how effort distributes across the engagement, with primary activities and "
    "the associated deliverable for each month."
))

# Monthly effort allocation table
effort_header = [
    Paragraph("Month", S['table_header']),
    Paragraph("Hours", S['table_header']),
    Paragraph("Primary Activities", S['table_header']),
    Paragraph("Deliverable", S['table_header']),
    Paragraph("Cumulative", S['table_header']),
]
effort_data = [
    effort_header,
    [Paragraph("May", S['table_cell_bold']),
     Paragraph("28", S['table_cell']),
     Paragraph("Document collection and intake; logic model development; evaluation plan drafting; initial stakeholder interviews; first TSC/IDEC observation", S['table_cell']),
     Paragraph("Evaluation Plan", S['table_cell']),
     Paragraph("$4,200", S['table_cell_right'])],
    [Paragraph("June", S['table_cell_bold']),
     Paragraph("28", S['table_cell']),
     Paragraph("Retroactive Q1–Q2 document review; stakeholder interviews (RIPCA staff, OHS, chiefs); meeting observation; content analysis of media materials; Q1–Q2 brief writing", S['table_cell']),
     Paragraph("Q1–Q2 Brief", S['table_cell']),
     Paragraph("$8,400", S['table_cell_right'])],
    [Paragraph("July", S['table_cell_bold']),
     Paragraph("22", S['table_cell']),
     Paragraph("Q3 real-time data collection; meeting observation (TSC, IDEC); training event observation; BAT deployment review; DRE program tracking", S['table_cell']),
     Paragraph("—", S['table_cell']),
     Paragraph("$11,700", S['table_cell_right'])],
    [Paragraph("August", S['table_cell_bold']),
     Paragraph("24", S['table_cell']),
     Paragraph("Q3 stakeholder interviews; data analysis; Q3 brief drafting; judicial collaboration assessment; database development status review", S['table_cell']),
     Paragraph("Q3 Brief", S['table_cell']),
     Paragraph("$15,300", S['table_cell_right'])],
    [Paragraph("September", S['table_cell_bold']),
     Paragraph("20", S['table_cell']),
     Paragraph("Q4 data collection (transitioning to remote); document review of monthly reports and meeting minutes; follow-up interviews", S['table_cell']),
     Paragraph("—", S['table_cell']),
     Paragraph("$18,300", S['table_cell_right'])],
    [Paragraph("October", S['table_cell_bold']),
     Paragraph("30", S['table_cell']),
     Paragraph("Q4 analysis and brief writing; final report drafting — implementation fidelity assessment, cross-domain synthesis, recommendations for FFY2027", S['table_cell']),
     Paragraph("Q4 Brief +\nFinal Report", S['table_cell']),
     Paragraph("$22,800", S['table_cell_right'])],
    [Paragraph("November", S['table_cell_bold']),
     Paragraph("15", S['table_cell']),
     Paragraph("Stakeholder presentation preparation and delivery; final report revisions based on RIPCA/OHS feedback; engagement close-out", S['table_cell']),
     Paragraph("Presentation", S['table_cell']),
     Paragraph("<b>$25,050</b>", S['table_cell_right'])],
]

# Totals row
effort_data.append([
    Paragraph("<b>Total</b>", S['table_cell_bold']),
    Paragraph("<b>167</b>", S['table_cell_bold']),
    Paragraph("", S['table_cell']),
    Paragraph("", S['table_cell']),
    Paragraph("<b>$25,050</b>", S['table_cell_right']),
])

effort_table = Table(effort_data, colWidths=[0.65*inch, 0.5*inch, 2.65*inch, 0.95*inch, 0.85*inch])
effort_table.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), DATA),
    ('TEXTCOLOR', (0,0), (-1,0), PAPER),
    ('BACKGROUND', (0,1), (-1,-2), PAPER),
    ('ROWBACKGROUNDS', (0,1), (-1,-2), [PAPER, DATA_WASH]),
    # Totals row
    ('BACKGROUND', (0,-1), (-1,-1), INK),
    ('TEXTCOLOR', (0,-1), (-1,-1), PAPER),
    ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
    ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ('TOPPADDING', (0,0), (-1,-1), 5),
    ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ('LEFTPADDING', (0,0), (-1,-1), 6),
    ('RIGHTPADDING', (0,0), (-1,-1), 6),
    ('GRID', (0,0), (-1,-1), 0.5, RULE),
    ('LINEABOVE', (0,0), (-1,0), 1.5, DATA),
    ('LINEABOVE', (0,-1), (-1,-1), 1.5, INK),
]))
story.append(effort_table)
story.append(Spacer(1, 8))

story.append(callout(
    "Note: Hours are estimates that reflect typical evaluation workflow. The fixed fee of $25,000 applies "
    "regardless of minor monthly variation. Invoices will reflect actual hours worked each month."
))

story.append(Spacer(1, 12))

# ── QUALIFICATIONS ──
story.append(section_header("5. Evaluator Qualifications"))
story.append(thin_rule())

story.append(body(
    "<b>Isaac Rubinstein, MPH</b> — Independent program evaluator based in Rhode Island. "
    "Six years designing evaluation and measurement systems for publicly funded programs, "
    "with a focus on healthcare systems change, implementation fidelity, and participatory methods."
))

story.append(sub_header("Education"))
story.append(bullet("<b>Master of Public Health (MPH)</b> — University of Washington School of Public Health. "
    "Formal evaluation training in program evaluation design, mixed methods, logic model development, "
    "and utilization-focused evaluation (HSERV 522)."))

story.append(sub_header("Relevant Experience"))
story.append(bullet(
    "<b>Enterprise Change Management, Seattle Children's Hospital</b> — Evaluated the implementation of "
    "an enterprise clinical/scheduling system across 40+ clinics. Designed a mixed-methods evaluation "
    "combining system usage data, direct observation in 8 clinics, and structured interviews with 22 clinicians. "
    "Quarterly reporting to the operational steering committee. Identified implementation gaps that led to "
    "targeted resource reallocation — not additional training."
))
story.append(bullet(
    "<b>Central Providence Unidos / One Neighborhood Builders (CPU-HEZ)</b> — Led evaluation and data strategy "
    "for a community-based Health Equity Zone in Central Providence, RI. Designed a shared measurement system "
    "spanning 60+ community partner organizations, built indicator frameworks and data dashboards across health, "
    "education, housing, and economic domains. Developed evaluation plans, facilitated participatory evaluation "
    "processes with community partners, and produced cross-program outcome data used by funders and coalition "
    "leadership for strategic planning and resource allocation."
))

story.append(sub_header("Methods Competency"))
story.append(body(
    "Mixed-methods evaluation design, implementation fidelity assessment, stakeholder interviews, "
    "meeting observation, document review, survey design, logic model development, theory of change, "
    "qualitative coding and analysis, output/outcome tracking, quarterly and annual evaluation reporting, "
    "data visualization, and evaluation capacity building."
))

story.append(PageBreak())

# ════════════════════════════════════════════════
# PAGE 7: INDEPENDENCE, LOGISTICS, TERMS
# ════════════════════════════════════════════════

story.append(section_header("6. Independence and Ethics"))
story.append(thin_rule())
story.append(body(
    "I have no prior contracts, employment, or financial relationships with RIPCA, the RIDOT Office on "
    "Highway Safety, the Rhode Island State Police, or any municipal law enforcement agency in Rhode Island. "
    "This engagement begins with full independence."
))
story.append(body(
    "Throughout the evaluation, I will maintain strict separation between evaluation and program delivery. "
    "I will observe meetings and events but not participate as a program contributor. I will collect and report "
    "data objectively and protect the confidentiality of interview participants. All evaluation activities "
    "will adhere to accepted evaluation ethics standards, including the American Evaluation Association's "
    "Guiding Principles for Evaluators."
))

story.append(section_header("7. Logistics and Availability"))
story.append(thin_rule())
story.append(body(
    "I am currently based in Rhode Island and available for all in-person meeting observation, site visits, "
    "and stakeholder interviews through August 2026. Beginning in <b>September 2026</b>, I will be relocating "
    "to Oslo, Norway. I am disclosing this upfront because it is material to the engagement structure."
))
story.append(body(
    "The practical impact is minimal. I will be on the ground in Rhode Island for the first four months of the "
    "engagement — the critical period covering start-up, the retroactive Q1–Q2 review, real-time Q3 observation, "
    "and the bulk of stakeholder interviews. The remaining evaluation work — Q4 analysis, final report writing, "
    "and the stakeholder presentation — is desk-based and fully executable remotely."
))
story.append(body(
    "The Oslo time zone (Central European Time) is 6 hours ahead of Eastern. Morning meetings in Rhode Island "
    "correspond to afternoon in Oslo — workable for regular check-ins and interviews. Quarterly deliverables "
    "and the final report are unaffected by location."
))
story.append(callout(
    "If in-person attendance is required for a specific event after September 2026, I can arrange local "
    "subcontractor support for observation or coordinate travel in advance. The stakeholder presentation "
    "in November 2026 can be delivered in person if scheduled with adequate lead time, or via video conference."
))

story.append(Spacer(1, 16))
story.append(section_header("8. Data and Document Requests"))
story.append(thin_rule())
story.append(body(
    "To begin the evaluation efficiently, I will need access to the following materials within the first "
    "two weeks of the engagement. I will provide a formal data collection matrix as part of the Evaluation Plan."
))

story.append(sub_header("From RIPCA"))
story.append(bullet("Monthly reports submitted to OHS (October 2025 – present)"))
story.append(bullet("IDEC meeting minutes and attendance records (all FFY2026)"))
story.append(bullet("Traffic Safety Coalition meeting notes (all FFY2026)"))
story.append(bullet("BAT Vehicle deployment schedules and usage logs"))
story.append(bullet("Training records — sessions delivered, attendance by agency"))
story.append(bullet("Media and communications samples (press releases, social media posts, newsletters)"))
story.append(bullet("DRE call-out system records and enrollment/certification data"))
story.append(bullet("Status updates on both proposed databases (impaired driving arrests, traffic stop portal)"))
story.append(bullet("Current organizational chart with positions filled and start dates"))

story.append(sub_header("From OHS (RIPCA to facilitate introduction)"))
story.append(bullet("OHS program manager contact for the RIPCA grant"))
story.append(bullet("FFY2026 approved work plan (OHS-signed version)"))
story.append(bullet("OHS expectations for evaluation deliverable format and review process"))
story.append(bullet("Prior year evaluation reports (FFY2024, FFY2025) if available"))

story.append(PageBreak())

# ════════════════════════════════════════════════
# PAGE 8: TERMS & SIGNATURE
# ════════════════════════════════════════════════

story.append(section_header("9. Terms and Conditions"))
story.append(thin_rule())

story.append(sub_header("Engagement Structure"))
story.append(body(
    "This is an independent contractor engagement. Isaac Rubinstein will perform all evaluation activities "
    "as a sole proprietor (Rubinstein Productions LLC). No employer-employee relationship is created by this "
    "agreement."
))

story.append(sub_header("Scope Changes"))
story.append(body(
    "If RIPCA or OHS requests evaluation activities beyond the scope described in this proposal, I will "
    "provide a written scope amendment with associated costs for approval before beginning additional work. "
    "No additional charges will be incurred without prior written agreement."
))

story.append(sub_header("Confidentiality"))
story.append(body(
    "I will protect the confidentiality of all stakeholder interview responses and any sensitive data shared "
    "for evaluation purposes. Individual responses will not be attributed by name in any deliverable without "
    "explicit consent. Aggregate findings and de-identified quotes may be included in reports."
))

story.append(sub_header("Intellectual Property"))
story.append(body(
    "All evaluation deliverables produced under this engagement — the evaluation plan, quarterly briefs, "
    "final report, and presentation materials — become the property of RIPCA and OHS upon delivery and payment."
))

story.append(sub_header("Termination"))
story.append(body(
    "Either party may terminate this engagement with 30 days' written notice. In the event of early termination, "
    "payment will be prorated based on deliverables completed and hours worked to date."
))

story.append(Spacer(1, 30))

# Signature block
story.append(ochre_rule())
story.append(Spacer(1, 8))

sig_data = [
    [Paragraph("<b>Submitted by:</b>", S['body']), Paragraph("", S['body'])],
    [Paragraph("", S['body']), Paragraph("", S['body'])],
    [Paragraph("_" * 40, S['body']), Paragraph("_" * 40, S['body'])],
    [Paragraph("Isaac Rubinstein, MPH", S['meta']), Paragraph("Sidney Wordell, Executive Director", S['meta'])],
    [Paragraph("Independent Evaluator", S['meta']), Paragraph("Rhode Island Police Chiefs Association", S['meta'])],
    [Paragraph("", S['body']), Paragraph("", S['body'])],
    [Paragraph("Date: _______________", S['meta']), Paragraph("Date: _______________", S['meta'])],
]
sig_table = Table(sig_data, colWidths=[3*inch, 3*inch])
sig_table.setStyle(TableStyle([
    ('VALIGN', (0,0), (-1,-1), 'BOTTOM'),
    ('TOPPADDING', (0,0), (-1,-1), 2),
    ('BOTTOMPADDING', (0,0), (-1,-1), 2),
    ('LEFTPADDING', (0,0), (-1,-1), 0),
]))
story.append(sig_table)

story.append(Spacer(1, 24))
story.append(Paragraph(
    "Isaac Rubinstein  ·  isaac@isaacrubinstein.com  ·  Rhode Island",
    S['meta']
))

# ── BUILD ──
doc.build(story, onFirstPage=first_page, onLaterPages=later_pages)
print(f"PDF created: {OUTPUT_FILE}")
