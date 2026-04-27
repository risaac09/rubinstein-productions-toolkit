#!/usr/bin/env python3
"""
Campus Compact CAP Measurement & Evaluation Consultant Proposal
Isaac Rubinstein — Professional PDF

Visual identity reused verbatim from build_proposal_RIPCA_REFERENCE.py.
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
OUTPUT_DIR = "/Users/isaacrubinstein/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second Brain/03 Projects/Evaluation Consultancy/02 Prospects/CampusCompact-CAP-2026"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "Rubinstein-CampusCompact-CAP-Proposal.pdf")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── STYLES ──
def make_styles():
    s = {}
    s['title'] = ParagraphStyle('Title', fontName='Helvetica-Bold', fontSize=26, leading=32,
        textColor=INK, spaceAfter=6, alignment=TA_LEFT, letterSpacing=-0.5)
    s['subtitle'] = ParagraphStyle('Subtitle', fontName='Helvetica', fontSize=13, leading=18,
        textColor=SLATE, spaceAfter=14, alignment=TA_LEFT)
    s['h1'] = ParagraphStyle('H1', fontName='Helvetica-Bold', fontSize=17, leading=22,
        textColor=INK, spaceBefore=16, spaceAfter=6, borderWidth=0)
    s['h2'] = ParagraphStyle('H2', fontName='Helvetica-Bold', fontSize=13, leading=17,
        textColor=OCHRE, spaceBefore=10, spaceAfter=5)
    s['h3'] = ParagraphStyle('H3', fontName='Helvetica-Bold', fontSize=11, leading=14,
        textColor=DATA, spaceBefore=8, spaceAfter=4)
    s['body'] = ParagraphStyle('Body', fontName='Helvetica', fontSize=10.5, leading=14,
        textColor=GRAPHITE, spaceAfter=5, alignment=TA_JUSTIFY)
    s['body_tight'] = ParagraphStyle('BodyTight', fontName='Helvetica', fontSize=10, leading=14.5,
        textColor=GRAPHITE, spaceAfter=4, alignment=TA_JUSTIFY)
    s['bullet'] = ParagraphStyle('Bullet', fontName='Helvetica', fontSize=10.5, leading=15,
        textColor=GRAPHITE, spaceAfter=4, leftIndent=18, bulletIndent=6, alignment=TA_LEFT)
    s['meta'] = ParagraphStyle('Meta', fontName='Helvetica', fontSize=9.5, leading=14,
        textColor=SLATE, spaceAfter=4)
    s['callout'] = ParagraphStyle('Callout', fontName='Helvetica-Oblique', fontSize=10.5, leading=15,
        textColor=DATA, spaceAfter=8, leftIndent=12, rightIndent=12)
    s['table_header'] = ParagraphStyle('TableHeader', fontName='Helvetica-Bold', fontSize=9.5, leading=13,
        textColor=PAPER, alignment=TA_LEFT)
    s['table_cell'] = ParagraphStyle('TableCell', fontName='Helvetica', fontSize=9.5, leading=13,
        textColor=GRAPHITE, alignment=TA_LEFT)
    s['table_cell_right'] = ParagraphStyle('TableCellRight', fontName='Helvetica', fontSize=9.5, leading=13,
        textColor=GRAPHITE, alignment=TA_RIGHT)
    s['table_cell_bold'] = ParagraphStyle('TableCellBold', fontName='Helvetica-Bold', fontSize=9.5, leading=13,
        textColor=GRAPHITE, alignment=TA_LEFT)
    s['footer'] = ParagraphStyle('Footer', fontName='Helvetica', fontSize=8, leading=10,
        textColor=SLATE, alignment=TA_CENTER)
    s['page_header'] = ParagraphStyle('PageHeader', fontName='Helvetica', fontSize=8, leading=10,
        textColor=SLATE, alignment=TA_RIGHT)
    s['small'] = ParagraphStyle('Small', fontName='Helvetica', fontSize=9, leading=13,
        textColor=SLATE, spaceAfter=4)
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

def sub_sub_header(text):
    return Paragraph(text, S['h3'])

def body(text):
    return Paragraph(text, S['body'])

def body_tight(text):
    return Paragraph(text, S['body_tight'])

def small(text):
    return Paragraph(text, S['small'])

# ── PAGE TEMPLATES ──
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
        "Isaac Rubinstein  ·  Independent Program Evaluator  ·  isaac@isaacrubinstein.com  ·  Rhode Island")
    canvas.restoreState()

def later_pages(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.5)
    canvas.line(72, letter[1] - 36, letter[0] - 72, letter[1] - 36)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(SLATE)
    canvas.drawRightString(letter[0] - 72, letter[1] - 30,
        "Campus Compact CAP M&E Proposal  ·  Isaac Rubinstein")
    canvas.drawCentredString(letter[0]/2, 36, f"Page {doc.page}")
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
# COVER
# ════════════════════════════════════════════════

story.append(Spacer(1, 24))
story.append(Paragraph("Proposal for Measurement<br/>&amp; Evaluation Consultant", S['title']))
story.append(Spacer(1, 4))
story.append(Paragraph(
    "Campus Action Planning for Civic &amp; Community Engagement (CAP)",
    S['subtitle']
))
story.append(ochre_rule())

meta_data = [
    ["Prepared for:", "Alexis Bucknam, Project Director, Campus Compact"],
    ["Prepared by:", "Isaac Rubinstein, MPH. Independent Evaluator"],
    ["Date:", "April 28, 2026"],
    ["Engagement period:", "May 1, 2026 – March 31, 2027"],
    ["Total fixed fee:", "$40,000 (inclusive of travel)"],
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

# ════════════════════════════════════════════════
# ABSTRACT
# ════════════════════════════════════════════════

story.append(section_header("Abstract"))
story.append(thin_rule())
story.append(body(
    "CAP asks ten activities (a framework, a resources hub, symposia, institutes, a 20-team Innovation Cohort, "
    "coaching, reporting metrics, webinars, the Chief Engagement Officer Learning Community, and a fellowship) "
    "to reaffirm higher education's public purpose by helping institutions put power and resources behind equity "
    "and democracy. Evaluating that kind of work needs a measurement system legible to funders, useful to "
    "participants while the program is running, and able to capture institutional change rather than only "
    "documented activity."
))
story.append(body(
    "This proposal lays out a mixed-methods process and outcome evaluation. I build a CAP-specific logic model "
    "with the Campus Compact team in the first month, then use it as the spine of every report. The methodology "
    "pairs standard implementation tracking (participation, fidelity, reach) with cohort-level developmental "
    "evaluation for the Innovation Cohort and the Chief Engagement Officer Learning Community. For the "
    "future-oriented Impact Measurement Framework I propose a Bilingual Dashboard: institutional accountability "
    "indicators alongside capacity and culture indicators that show whether participating institutions are "
    "changing how they allocate resources for civic and community engagement."
))
story.append(body(
    "The engagement runs May 2026 through March 2027 at a fixed fee of <b>$40,000</b>, inclusive of travel for "
    "two Institute observations. Deliverables land while CAP decisions are still open, not after. Isaac Rubinstein "
    "holds an MPH from the University of Washington with formal training in program evaluation, designed the "
    "shared measurement system for a 60+ partner Health Equity Zone in Rhode Island, and is a member of the "
    "American Evaluation Association and the Evaluation Network of Rhode Island."
))


# ════════════════════════════════════════════════
# METHODOLOGY
# ════════════════════════════════════════════════

story.append(section_header("Proposed Assessment and Evaluation Methodology"))
story.append(thin_rule())
story.append(body(
    "This is a process evaluation of a multi-component civic engagement initiative, supplemented by outcome "
    "measurement at the cohort and institutional levels where the data supports it. The design uses mixed "
    "methods: quantitative output and participation tracking combined with qualitative data collection at the "
    "activity, cohort, and institutional levels."
))

story.append(sub_header("Evaluation Framework"))
story.append(body(
    "I build a CAP-specific logic model in the first three weeks with the Campus Compact CAP team and include "
    "it in the Evaluation Plan. The model maps the ten CAP activities to short-term outputs (participation, "
    "deliverable production, resource reach), intermediate outcomes (institutional planning capacity, peer "
    "learning, cross-team knowledge exchange), and longer-term outcomes (institutional commitments to civic "
    "and community engagement, and changes in how participating institutions allocate resources for equity "
    "and democracy). Once approved, the model becomes the spine of every interim report, the final outcome "
    "reports, and the Impact Measurement Framework."
))
story.append(body(
    "The framework tracks <b>process measures</b> (were activities implemented as planned, with what fidelity "
    "and reach? did the Innovation Cohort experience the program as designed?) and <b>outcome measures</b> "
    "(did participation contribute to changes in institutional planning, peer-network density, leader capacity, "
    "or commitments to equity and democracy work?). Most institutional change cannot be cleanly attributed to "
    "CAP. The evaluation uses contribution analysis: documenting what CAP delivered, how well it was implemented, "
    "and what evidence suggests it contributed alongside other forces."
))
story.append(body(
    "The methodology follows the American Evaluation Association's <i>Guiding Principles for Evaluators</i> and "
    "draws on established practice in higher-education civic engagement assessment, including developmental "
    "evaluation (Patton) for the Innovation Cohort and peer-learning evaluation methods for the Chief "
    "Engagement Officer Learning Community."
))

story.append(sub_header("Methods and Application Across the Ten CAP Activities"))

methods_header = [
    Paragraph("Method", S['table_header']),
    Paragraph("Application", S['table_header']),
    Paragraph("CAP Activities", S['table_header']),
]
methods_data = [
    methods_header,
    [Paragraph("Document review", S['table_cell_bold']),
     Paragraph("CAP Framework, Resources hub, symposia materials, Institute curricula, webinar recordings, fellowship deliverables", S['table_cell']),
     Paragraph("Framework, Hub, Symposia, Institutes, Webinars, Fellowship", S['table_cell'])],
    [Paragraph("Output / participation tracking", S['table_cell_bold']),
     Paragraph("Registration, attendance, completion, hub utilization, webinar reach, coaching call cadence, reporting metric submission rates", S['table_cell']),
     Paragraph("All ten activities", S['table_cell'])],
    [Paragraph("Pre/post + 90-day surveys", S['table_cell_bold']),
     Paragraph("Brief instruments at Institutes, Symposia, and key webinars; 90-day follow-up assessing application back at the institution", S['table_cell']),
     Paragraph("Symposia, Institutes, Webinars, Innovation Cohort", S['table_cell'])],
    [Paragraph("Semi-structured interviews", S['table_cell_bold']),
     Paragraph("18–24 interviews: CAP team, Innovation Cohort team leads (sampled), CEng LC members, fellowship participants, sampled independent campus-community team representatives", S['table_cell']),
     Paragraph("Innovation Cohort, CEng LC, Fellowship, independent teams", S['table_cell'])],
    [Paragraph("Direct observation", S['table_cell_bold']),
     Paragraph("At least two Institutes attended in person; remote observation of selected cohort convenings, coaching calls, and CEng sessions where appropriate", S['table_cell']),
     Paragraph("Institutes, Innovation Cohort, Coaching, CEng LC", S['table_cell'])],
    [Paragraph("Content / use analysis", S['table_cell_bold']),
     Paragraph("Resources hub utilization patterns; analysis of Reporting Metrics submissions for completeness, consistency, and the questions they enable", S['table_cell']),
     Paragraph("Resources hub, Reporting Metrics", S['table_cell'])],
    [Paragraph("Cohort developmental evaluation", S['table_cell_bold']),
     Paragraph("Brief monthly check-in artifacts with cohort facilitators to surface emerging patterns, mid-course adjustments, and early signals across the 20 teams", S['table_cell']),
     Paragraph("Innovation Cohort", S['table_cell'])],
]
methods_table = Table(methods_data, colWidths=[1.6*inch, 3.2*inch, 1.6*inch])
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
story.append(Spacer(1, 6))
story.append(methods_table)
story.append(Spacer(1, 12))

story.append(body(
    "Each activity gets the methods its complexity warrants. The Innovation Cohort and CEng Learning Community "
    "get the deepest qualitative attention because peer learning and institutional change are where the "
    "program's theory of change concentrates. Symposia, webinars, and the Resources hub get participation, "
    "satisfaction, and use data plus a content review."
))

story.append(sub_header("The CAP Impact Measurement Framework: a Bilingual Dashboard"))
story.append(body(
    "The Impact Measurement Framework is the deliverable with the longest tail. To meet both funder "
    "accountability needs and the program's substantive ambitions, I propose a <b>Bilingual Dashboard</b> that "
    "pairs two measurement languages:"
))
story.append(bullet(
    "<b>Institutional accountability indicators:</b> participation, completion, deliverable production, reach, "
    "and institutional commitments documented in writing. These satisfy reporting requirements and demonstrate "
    "accountability."
))
story.append(bullet(
    "<b>Capacity and culture indicators:</b> institutional planning maturity, peer-network density across "
    "cohorts, role definition for Chief Engagement Officers, and signals of change in how participating "
    "institutions allocate resources for civic and community engagement work. These show whether the program "
    "is producing institutional change, not only documented activity."
))
story.append(body(
    "Neither set is sufficient on its own. The Reporting Templates that accompany the Framework will be "
    "designed for institutions to complete in under 30 minutes per reporting period, with built-in fields "
    "that populate the dashboard."
))

story.append(sub_header("Independent Campus-Community Teams"))
story.append(body(
    "The Process and Outcome Evaluation Reports cover both the Innovation Cohort and a sample of independent "
    "campus-community teams. Independent teams get a lighter-touch approach: a structured intake form at the "
    "start of CAP engagement, a mid-point progress survey, a final reflection instrument, and a small number "
    "of qualitative interviews with sampled teams. This produces comparable data without the cost of full "
    "developmental evaluation across both populations."
))

story.append(sub_header("Limitations and How I Handle Them"))
story.append(body(
    "Three things worth naming up front. First, institutional attribution is bounded; institutional change "
    "rarely traces cleanly to a single program. The evaluation uses contribution analysis and is explicit "
    "about the inferential limits. Second, the engagement starts one month after fellowship recruitment and "
    "runs alongside an Institute schedule that was set before the evaluator was selected. The Evaluation "
    "Plan includes a documentation request to the CAP team in the first two weeks to capture what has already "
    "happened. Third, when data is genuinely unavailable (for example, institutional resource flows that "
    "participating institutions cannot share) the evaluation names the gap and tracks it as a finding in its "
    "own right rather than treating the domain as unevaluable. This is program-level evaluation for quality "
    "improvement and accountability; it is not human subjects research and does not require IRB review."
))


# ════════════════════════════════════════════════
# WORK STYLE STATEMENT
# ════════════════════════════════════════════════

story.append(section_header("Work Style Statement"))
story.append(thin_rule())
story.append(body(
    "Whether an evaluation gets used usually comes down to whether the people who need the findings helped "
    "shape the questions. I work from that premise."
))
story.append(body(
    "I design deliverables for use, not for shelving. Every interim report leads with a one-page executive "
    "summary that names a small number of decisions the reader can act on now. I co-develop findings in "
    "conversation with the Campus Compact team, and where appropriate with cohort participants, instead of "
    "delivering them cold at the end. Between deliverables I hold a thirty-minute monthly check-in with the "
    "project director, async-prepared agenda, focused on what the data is starting to show and what to do "
    "about it before the next deliverable lands. This keeps the evaluation responsive without crossing into "
    "program delivery."
))
story.append(body(
    "I bring facilitation skills into the qualitative data collection itself. Institutional leaders and cohort "
    "participants give richer answers when the interviewer can hold a conversation instead of working through "
    "a script, and richer qualitative data produces findings that institutions recognize and act on."
))
story.append(body(
    "On day-to-day collaboration: I default to async written communication with a 24-hour response window on "
    "weekdays, and propose synchronous time only when it is the right tool for the conversation. I am "
    "transparent about what I am working on and where I am, including any travel. I name disagreements early "
    "and hold them loosely. I want to be told when something I have produced is not landing, and I revise "
    "without ceremony when the feedback is grounded in the program's needs."
))
story.append(body(
    "I am the sole consultant on this engagement. No subcontractors, one point of accountability, one voice "
    "across the deliverables."
))

# ════════════════════════════════════════════════
# TIMELINE & DELIVERABLES
# ════════════════════════════════════════════════

story.append(section_header("Proposed Timeline and Deliverables"))
story.append(thin_rule())
story.append(body(
    "The engagement runs May 1, 2026 through March 31, 2027. I sequence deliverables so each one informs the "
    "next, and so findings land while CAP decisions are still open."
))

timeline_header = [
    Paragraph("Deliverable", S['table_header']),
    Paragraph("Description", S['table_header']),
    Paragraph("Target Date", S['table_header']),
]
timeline_data = [
    timeline_header,
    [Paragraph("Evaluation Plan + data collection tools", S['table_cell_bold']),
     Paragraph("Logic model, evaluation questions, indicators by activity, data sources, methods matrix, instruments (survey templates, interview protocols, observation guides), reporting cadence. Submitted for review and revised once.", S['table_cell']),
     Paragraph("May 29, 2026", S['table_cell'])],
    [Paragraph("CAP Interim Process Evaluation Report", S['table_cell_bold']),
     Paragraph("Implementation assessment of the first cycle (Framework, Resources hub launch, first Symposium, August Institute, opening months of Innovation Cohort and CEng LC). Domain-by-domain findings with fidelity indicators, participation and reach data, emerging recommendations.", S['table_cell']),
     Paragraph("Sept 18, 2026", S['table_cell'])],
    [Paragraph("CAP Interim Evaluation Report (mid-year)", S['table_cell_bold']),
     Paragraph("Cumulative process findings through the October Institute and mid-cohort point. Includes early outcome signals where data supports it, and a 'so what' section with adjustments before the December Institute.", S['table_cell']),
     Paragraph("Dec 11, 2026", S['table_cell'])],
    [Paragraph("CEng Learning Community Evaluation", S['table_cell_bold']),
     Paragraph("Standalone evaluation of the Chief Engagement Officer Learning Community: peer-learning quality, role-definition co-creation, member experience, and recommendations for future cohorts.", S['table_cell']),
     Paragraph("Jan 22, 2027", S['table_cell'])],
    [Paragraph("CAP Process + Outcome Evaluation Reports", S['table_cell_bold']),
     Paragraph("Two companion reports: one on the Innovation Cohort (full developmental and outcome evaluation across the 20 teams), one on the independent campus-community teams (lighter-touch outcome evaluation). Cross-cohort synthesis included.", S['table_cell']),
     Paragraph("Feb 26, 2027", S['table_cell'])],
    [Paragraph("CAP Impact Measurement Framework + Reporting Templates", S['table_cell_bold']),
     Paragraph("The future-oriented Bilingual Dashboard structure, indicator definitions, data sources, reporting cadence guidance, and the Reporting Templates participating institutions will use. Designed to outlive this engagement.", S['table_cell']),
     Paragraph("Mar 19, 2027", S['table_cell'])],
    [Paragraph("Monitoring + Tracking Dashboard", S['table_cell_bold']),
     Paragraph("Working dashboard implementing the Impact Measurement Framework, populated with available data through the end of the engagement, with documentation for ongoing maintenance by the Campus Compact team.", S['table_cell']),
     Paragraph("Mar 31, 2027", S['table_cell'])],
]
timeline_table = Table(timeline_data, colWidths=[1.7*inch, 3.5*inch, 1.0*inch])
timeline_table.setStyle(TableStyle([
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
story.append(Spacer(1, 6))
story.append(timeline_table)
story.append(Spacer(1, 12))

story.append(sub_header("Reporting Format"))
story.append(body(
    "Every interim and final report follows the same structure: a one-page executive summary leading with the "
    "decisions the reader can act on; findings organized by CAP activity (or by cohort, where appropriate) "
    "with implementation fidelity indicators; a data-gaps section that names what could not be assessed and "
    "why; recommendations tied directly to findings; and a data appendix with output and participation tables."
))

story.append(sub_header("How Findings Get Used"))
story.append(body(
    "Between deliverables I hold a 30-minute monthly check-in with the project director to surface emerging "
    "issues, coordinate observation opportunities, and confirm that the next deliverable is targeting the "
    "right questions. Each report includes a 'so what' section with specific adjustments the team can act on. "
    "The final reports and the Impact Measurement Framework are built for handoff: the Framework, Templates, "
    "and Dashboard become Campus Compact's, with the technical documentation needed to maintain them after "
    "this engagement closes."
))


# ════════════════════════════════════════════════
# EXAMPLES OF PRIOR EVALUATIONS
# ════════════════════════════════════════════════

story.append(section_header("Examples of Previous Successful Project Assessments and Evaluations"))
story.append(thin_rule())
story.append(body(
    "I picked the three engagements below because each maps to a different dimension of the CAP evaluation: "
    "shared measurement across many partner organizations, multi-site implementation evaluation across an "
    "institutional system, and current independent evaluation of a coordination and capacity-building grant."
))

# Case 1
story.append(sub_header("1. Central Providence Unidos Health Equity Zone: Shared measurement across 60+ partner organizations"))
story.append(small("<b>Role:</b> Associate Director of Evaluation &amp; Learning (FY2024–FY2025)"))
story.append(small("<b>Funders:</b> Rhode Island Department of Health, CDC Foundation, Blue Meridian Partners, Race Forward, United Way of Rhode Island, Reinvestment Fund, Commerce Rhode Island"))
story.append(body(
    "<b>Scope:</b> Designed and led the evaluation and shared measurement system for a Health Equity Zone "
    "collaborative spanning 60+ partner organizations, addressing health and economic inequities across "
    "housing, health, education, employment, and civic power."
))
story.append(body(
    "<b>Methods:</b> Three-layered framework combining Results-Based Accountability, the Water of Systems "
    "Change framework (Kania, Kramer, Senge 2018) at structural / relational / transformative depths, and "
    "embedded racial equity assessment. Mixed methods spanning population-level indicator tracking, partner "
    "survey design and analysis, key informant interviews, focus groups, and participatory observation. "
    "Co-designed a Data Academy with Brown University to build resident data literacy."
))
story.append(body(
    "<b>Outputs:</b> A shared 14-indicator Population Dashboard across six health-equity domains; an FY25 "
    "Action Plan with embedded evaluation metrics for every strategy; an Annual Collaborative Member Survey "
    "assessing backbone effectiveness and collective impact functioning; a Resident Advisory Council "
    "evaluation role with bi-annual member experience surveys; a bilingual Community Roadmap (English + Spanish)."
))
story.append(body(
    "<b>Why it is relevant to CAP:</b> This is the closest structural analogue to CAP. A multi-actor "
    "initiative in which dozens of organizations operate semi-independently around shared goals, and the "
    "central evaluation challenge is to produce a shared measurement system that serves both institutional "
    "accountability and substantive program improvement. Brown University was a co-design partner; Brown's "
    "Swearer Center is a Campus Compact member institution."
))

# Case 2
story.append(sub_header("2. Enterprise Change Management Evaluation, Seattle Children's Hospital: Multi-site implementation evaluation"))
story.append(small("<b>Role:</b> Lead evaluator for an enterprise system implementation across 40+ clinics"))
story.append(body(
    "<b>Scope:</b> Evaluated the multi-year implementation of a new clinical and scheduling system across the "
    "hospital's outpatient network, with quarterly reporting to the operational steering committee."
))
story.append(body(
    "<b>Methods:</b> Mixed-methods design combining system usage data analysis, direct observation in eight "
    "sampled clinics, and structured interviews with 22 clinicians spanning roles and clinic types. Quarterly "
    "reporting cadence aligned with operational decision points."
))
story.append(body(
    "<b>Outputs:</b> Quarterly evaluation briefs that identified specific implementation gaps in selected "
    "sites, leading to targeted resource reallocation and site-level support adjustments mid-implementation."
))
story.append(body(
    "<b>Why it is relevant to CAP:</b> Multi-site initiative, each site with its own implementation context, "
    "where the central evaluation question is implementation fidelity and uptake rather than the intervention's "
    "underlying validity, and where quarterly reporting was structured for operational use rather than "
    "retrospective summary. The Innovation Cohort's 20 teams present a similar challenge."
))

# Case 3
story.append(sub_header("3. Rhode Island Police Chiefs Association FFY2026 Highway Safety Grant: Current independent evaluation"))
story.append(small("<b>Role:</b> Independent evaluator (engagement period May–November 2026)"))
story.append(body(
    "<b>Scope:</b> Mixed-methods process evaluation of a federally funded coordination and capacity-building "
    "grant covering seven domains of activity (media and outreach, coalition engagement, enforcement resource "
    "utilization, judicial collaboration, impaired driving programs, training and leadership, strategic "
    "partnerships)."
))
story.append(body(
    "<b>Methods:</b> Logic model developed in the first two weeks and used as the spine of all reporting. "
    "Mixed methods including document review, output tracking, 8–12 stakeholder interviews, direct "
    "observation of monthly coalition meetings, content analysis, and post-training participant surveys. "
    "Contribution analysis used in lieu of attribution where appropriate."
))
story.append(body(
    "<b>Outputs in progress:</b> Evaluation Plan, four quarterly evaluation briefs, final evaluation report "
    "with FFY2027 recommendations, and a stakeholder presentation. Engagement currently underway."
))
story.append(body(
    "<b>Why it is relevant to CAP:</b> Current, active independent evaluation of a coordination grant. "
    "Closest direct parallel to the CAP engagement in funding model, deliverable structure, and engagement "
    "length. The methods and reporting cadence match what I propose here."
))

story.append(small("<i>A fuller portfolio of work samples is available on request.</i>"))


# ════════════════════════════════════════════════
# SUMMARY OF RELEVANT EXPERIENCE
# ════════════════════════════════════════════════

story.append(section_header("Summary of Relevant Evaluation Experience"))
story.append(thin_rule())
story.append(body(
    "<b>Isaac Rubinstein, MPH.</b> Independent program evaluator based in Rhode Island. Six years designing "
    "and leading evaluation and shared measurement systems for publicly funded coalition, capacity-building, "
    "and place-based initiatives, with a focus on participatory and utilization-focused methods."
))

story.append(sub_header("Education"))
story.append(body(
    "Master of Public Health, University of Washington School of Public Health. Formal training in program "
    "evaluation design, mixed methods, logic model development, and utilization-focused evaluation (HSERV 522)."
))

story.append(sub_header("Most Relevant Prior Roles"))
story.append(bullet(
    "<b>Associate Director of Evaluation &amp; Learning, Central Providence Unidos / One Neighborhood Builders.</b> "
    "Designed and led the shared measurement system for a 60+ partner Health Equity Zone in Central Providence, "
    "Rhode Island. Built a 14-indicator population dashboard across six health-equity domains; embedded "
    "evaluation metrics into the collaborative's annual action plan; designed and administered the Annual "
    "Collaborative Member Survey assessing backbone-organization effectiveness and collective-impact "
    "functioning; co-designed a Data Academy with Brown University to build resident data literacy. Used "
    "Results-Based Accountability, the Water of Systems Change framework, and embedded racial equity assessment."
))
story.append(bullet(
    "<b>Enterprise Change Management Evaluation, Seattle Children's Hospital.</b> Led mixed-methods "
    "evaluation of a clinical/scheduling system implementation across 40+ clinics, with quarterly reporting "
    "to the operational steering committee that drove mid-implementation resource reallocation."
))
story.append(bullet(
    "<b>Independent evaluator, Rhode Island Police Chiefs Association FFY2026 Highway Safety Grant.</b> "
    "Currently delivering a mixed-methods process evaluation across seven program domains for a federally "
    "funded coordination grant (May–November 2026)."
))

story.append(sub_header("Methods Competency"))
story.append(body(
    "Mixed-methods evaluation design; implementation fidelity assessment; logic model and theory of change "
    "development; stakeholder interviews; participatory and developmental evaluation; meeting and program "
    "observation; document review; survey design; qualitative coding and analysis; output and outcome "
    "tracking; quarterly and annual evaluation reporting; data visualization and dashboard design; "
    "evaluation capacity building."
))

story.append(sub_header("Higher-Education Engagement"))
story.append(body(
    "Co-designed the Data Academy with Brown University as part of the CPU-HEZ work and presented twice at "
    "Brown University on the Central Providence Unidos shared-measurement work. Embedded in Rhode Island's "
    "evaluation community through the Evaluation Network of Rhode Island (ENRI), with active working "
    "relationships at Brown University's Swearer Center for Public Service (a Campus Compact member "
    "institution) and across the local higher-education evaluation network. Professional references "
    "available on request, including Dr. Dan Turner (Brown / Swearer Center CEDEC, community-engaged "
    "evaluation) and Dr. Cynthia Roberts (RICADV, ENRI co-founder, equity-centered evaluation)."
))

story.append(sub_header("Professional Affiliations"))
story.append(body("American Evaluation Association (AEA). Evaluation Network of Rhode Island (ENRI)."))


# ════════════════════════════════════════════════
# BUDGET
# ════════════════════════════════════════════════

story.append(section_header("Budget and Budget Narrative"))
story.append(thin_rule())

fee_header = [
    Paragraph("Component", S['table_header']),
    Paragraph("Detail", S['table_header']),
    Paragraph("Amount", S['table_header']),
]
fee_data = [
    fee_header,
    [Paragraph("Hourly rate", S['table_cell_bold']),
     Paragraph("All evaluation activities: planning, instrument design, data collection, analysis, reporting, observation, interviews, dashboard build, presentations", S['table_cell']),
     Paragraph("$150 / hour", S['table_cell'])],
    [Paragraph("Estimated effort", S['table_cell_bold']),
     Paragraph("250 hours across the 11-month engagement (avg 23 hours / month, weighted heavier in start-up, around the August and October Institutes, and during final deliverable production in February and March)", S['table_cell']),
     Paragraph("—", S['table_cell'])],
    [Paragraph("Labor subtotal", S['table_cell_bold']),
     Paragraph("250 hours × $150 / hour", S['table_cell']),
     Paragraph("$37,500", S['table_cell'])],
    [Paragraph("Travel", S['table_cell_bold']),
     Paragraph("Two Institute observation visits at an estimated $1,250 each (airfare, ground transportation, two nights' lodging, per diem). Documented travel only; receipts available on request", S['table_cell']),
     Paragraph("$2,500", S['table_cell'])],
    [Paragraph("<b>Total fixed fee</b>", S['table_cell_bold']),
     Paragraph("Covers all six deliverables specified in this proposal", S['table_cell']),
     Paragraph("<b>$40,000</b>", S['table_cell'])],
]
fee_table = Table(fee_data, colWidths=[1.5*inch, 3.6*inch, 1.1*inch])
fee_table.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), DATA),
    ('TEXTCOLOR', (0,0), (-1,0), PAPER),
    ('BACKGROUND', (0,1), (-1,-1), PAPER),
    ('ROWBACKGROUNDS', (0,1), (-1,-2), [PAPER, DATA_WASH]),
    ('BACKGROUND', (0,-1), (-1,-1), OCHRE_WASH),
    ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ('TOPPADDING', (0,0), (-1,-1), 6),
    ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ('LEFTPADDING', (0,0), (-1,-1), 8),
    ('RIGHTPADDING', (0,0), (-1,-1), 8),
    ('GRID', (0,0), (-1,-1), 0.5, RULE),
    ('LINEABOVE', (0,0), (-1,0), 1.5, DATA),
]))
story.append(Spacer(1, 6))
story.append(fee_table)
story.append(Spacer(1, 16))

story.append(sub_header("Monthly Effort Allocation"))

effort_header = [
    Paragraph("Month", S['table_header']),
    Paragraph("Hrs", S['table_header']),
    Paragraph("Primary Activities", S['table_header']),
    Paragraph("Deliverable", S['table_header']),
    Paragraph("Cum. Labor", S['table_header']),
]
effort_data = [
    effort_header,
    [Paragraph("May 2026", S['table_cell_bold']), Paragraph("25", S['table_cell_right']),
     Paragraph("Onboarding, document review, logic model development, Evaluation Plan drafting, instrument design, initial CAP team interviews", S['table_cell']),
     Paragraph("Evaluation Plan", S['table_cell']),
     Paragraph("$3,750", S['table_cell_right'])],
    [Paragraph("June 2026", S['table_cell_bold']), Paragraph("22", S['table_cell_right']),
     Paragraph("Instrument finalization; baseline data from Innovation Cohort intake; Resources hub utilization baseline; first Symposium observation (remote)", S['table_cell']),
     Paragraph("—", S['table_cell']),
     Paragraph("$7,050", S['table_cell_right'])],
    [Paragraph("July 2026", S['table_cell_bold']), Paragraph("22", S['table_cell_right']),
     Paragraph("Innovation Cohort early-implementation interviews; coaching call sampling; CEng LC opening session observation; analysis", S['table_cell']),
     Paragraph("—", S['table_cell']),
     Paragraph("$10,350", S['table_cell_right'])],
    [Paragraph("Aug 2026", S['table_cell_bold']), Paragraph("28", S['table_cell_right']),
     Paragraph("August Institute observation (in person, ~3 days on-site incl. travel); post-Institute survey administration; analysis", S['table_cell']),
     Paragraph("—", S['table_cell']),
     Paragraph("$14,550", S['table_cell_right'])],
    [Paragraph("Sept 2026", S['table_cell_bold']), Paragraph("24", S['table_cell_right']),
     Paragraph("Interim Process Report drafting and revision; cohort mid-implementation interviews", S['table_cell']),
     Paragraph("Interim Process Report", S['table_cell']),
     Paragraph("$18,150", S['table_cell_right'])],
    [Paragraph("Oct 2026", S['table_cell_bold']), Paragraph("28", S['table_cell_right']),
     Paragraph("October Institute observation (in person, ~3 days); CEng LC mid-point assessment; cohort developmental check-ins", S['table_cell']),
     Paragraph("—", S['table_cell']),
     Paragraph("$22,350", S['table_cell_right'])],
    [Paragraph("Nov 2026", S['table_cell_bold']), Paragraph("20", S['table_cell_right']),
     Paragraph("Mid-year data analysis and synthesis; Interim Evaluation Report drafting", S['table_cell']),
     Paragraph("—", S['table_cell']),
     Paragraph("$25,350", S['table_cell_right'])],
    [Paragraph("Dec 2026", S['table_cell_bold']), Paragraph("20", S['table_cell_right']),
     Paragraph("Interim Evaluation Report finalization; December Institute remote observation; cohort closing data collection", S['table_cell']),
     Paragraph("Mid-year Interim Report", S['table_cell']),
     Paragraph("$28,350", S['table_cell_right'])],
    [Paragraph("Jan 2027", S['table_cell_bold']), Paragraph("22", S['table_cell_right']),
     Paragraph("CEng Learning Community evaluation: deep interviews, member surveys, drafting", S['table_cell']),
     Paragraph("CEng LC Evaluation", S['table_cell']),
     Paragraph("$31,650", S['table_cell_right'])],
    [Paragraph("Feb 2027", S['table_cell_bold']), Paragraph("24", S['table_cell_right']),
     Paragraph("Innovation Cohort and independent-team outcome evaluation: final cohort interviews, outcome data analysis, drafting", S['table_cell']),
     Paragraph("Process + Outcome Reports", S['table_cell']),
     Paragraph("$35,250", S['table_cell_right'])],
    [Paragraph("Mar 2027", S['table_cell_bold']), Paragraph("15", S['table_cell_right']),
     Paragraph("Impact Measurement Framework + Reporting Templates; Dashboard build; documentation; engagement close-out", S['table_cell']),
     Paragraph("Impact Framework + Dashboard", S['table_cell']),
     Paragraph("$37,500", S['table_cell_right'])],
    [Paragraph("<b>Total</b>", S['table_cell_bold']), Paragraph("<b>250</b>", S['table_cell_right']),
     Paragraph("", S['table_cell']),
     Paragraph("", S['table_cell']),
     Paragraph("<b>$37,500 + $2,500 travel = $40,000</b>", S['table_cell_right'])],
]
effort_table = Table(effort_data, colWidths=[0.7*inch, 0.4*inch, 2.7*inch, 1.2*inch, 1.2*inch])
effort_table.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), DATA),
    ('TEXTCOLOR', (0,0), (-1,0), PAPER),
    ('BACKGROUND', (0,1), (-1,-1), PAPER),
    ('ROWBACKGROUNDS', (0,1), (-1,-2), [PAPER, DATA_WASH]),
    ('BACKGROUND', (0,-1), (-1,-1), OCHRE_WASH),
    ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ('TOPPADDING', (0,0), (-1,-1), 5),
    ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ('LEFTPADDING', (0,0), (-1,-1), 6),
    ('RIGHTPADDING', (0,0), (-1,-1), 6),
    ('GRID', (0,0), (-1,-1), 0.5, RULE),
    ('LINEABOVE', (0,0), (-1,0), 1.5, DATA),
]))
story.append(Spacer(1, 6))
story.append(effort_table)
story.append(Spacer(1, 12))

story.append(small(
    "<i>Hours are estimates reflecting typical evaluation workflow and expected variation across the engagement. "
    "The fixed fee of $40,000 applies regardless of minor monthly variation. Invoices are submitted monthly with "
    "a 15-day payment window and include a brief summary of hours worked and activities completed. Travel is "
    "included in the total fee; no separate reimbursements are requested. No additional costs for materials, "
    "technology, or report production.</i>"
))

story.append(sub_header("Scope Boundaries"))
story.append(body(
    "This proposal covers the ten CAP activities and the six deliverables described in the RFP, on the timeline "
    "above. If Campus Compact requests additional evaluation activities beyond this scope (additional data "
    "collection instruments, supplementary reports, expanded stakeholder engagement, or in-person observation "
    "beyond the two Institute visits budgeted here), I will provide a written scope amendment with estimated "
    "hours and cost before beginning the work. No work outside the stated scope proceeds without mutual "
    "written agreement."
))

story.append(Spacer(1, 16))
story.append(thin_rule())
story.append(small(
    "<i>CV attached separately (does not count toward 10-page limit).</i>"
))

# ── BUILD ──
doc.build(story, onFirstPage=first_page, onLaterPages=later_pages)
print(f"PDF created: {OUTPUT_FILE}")
