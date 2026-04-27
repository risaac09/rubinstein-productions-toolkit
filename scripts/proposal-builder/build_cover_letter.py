#!/usr/bin/env python3
"""
Cover letter PDF generator — single page, branded, simple.

Usage: edit BODY_PARAGRAPHS, RECIPIENT, SUBJECT, OUTPUT_FILE for each new
cover letter. For now, hardcoded to the Cardea Social Impact Evaluation
Manager application.

Visual identity matches the proposal-builder scripts (INK title,
SLATE meta, GRAPHITE body, OCHRE accent rule).
"""
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_JUSTIFY, TA_CENTER
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
import os

# Brand
INK      = HexColor("#0F1729")
GRAPHITE = HexColor("#2A2D34")
SLATE    = HexColor("#5A6170")
OCHRE    = HexColor("#9A6B2F")
RULE     = HexColor("#D8D2C3")

# Output
OUTPUT_DIR = "/Users/isaacrubinstein/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second Brain/04 Career/Applications/Cardea-SocialImpactEvalMgr-2026-04"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "Rubinstein-Cardea-CoverLetter.pdf")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Letter content
SENDER_NAME = "Isaac Rubinstein, MPH"
SENDER_TITLE = "Public Health Evaluator"
SENDER_EMAIL = "isaac@isaacrubinstein.com"
SENDER_LOCATION = "Seattle, WA  ·  (206) 419-6888"
DATE_LINE = "April 27, 2026"
RECIPIENT = "Dear Cardea Hiring Team,"
SIGNATURE_NAME = "Isaac Rubinstein, MPH"

BODY_PARAGRAPHS = [
    "I am applying for the Social Impact Evaluation Manager position. I'm based in Seattle, "
    "finished my MPH at the University of Washington in March 2025, and currently lead change "
    "management readiness work at Seattle Children's Hospital. Before that, I spent two years as "
    "Associate Director of Evaluation and Learning at One Neighborhood Builders, where I built "
    "the shared measurement system for Central Providence Unidos — a Health Equity Zone spanning "
    "more than sixty partner organizations across health, housing, education, and economic "
    "mobility. Cardea's framing of \"co-creating solutions that center community strengths and "
    "wisdom\" describes exactly the kind of evaluation practice I have spent the past several "
    "years building, and I would welcome the chance to bring it to your team.",

    "The CPU-HEZ work is the closest analogue to what Cardea does. I designed and deployed a "
    "population-level dashboard built on a three-layer evaluation framework: Results-Based "
    "Accountability for institutional accountability, the Water of Systems Change framework for "
    "tracking structural / relational / transformative shifts, and embedded racial equity "
    "assessment. The dashboard tracked fourteen indicators across six health-equity domains — "
    "housing cost burden, eviction filings, insurance coverage by race/ethnicity, lead exposure, "
    "multilingual learner graduation rates, juvenile arrest rates, voter registration, and "
    "others — sourced from authoritative public datasets with documented limitations. I also "
    "designed and administered an annual Collaborative Member Survey assessing "
    "backbone-organization effectiveness and embedded a Resident Advisory Council in the "
    "evaluation process itself, with bi-annual surveys evaluating their experience as "
    "decision-makers.",

    "The Cardea position description names quantitative and qualitative tooling explicitly, and "
    "the work I have done maps onto it directly. I built the Central Providence Unidos dashboard "
    "in Tableau, work in R for quantitative analysis (built up during the MPH and used since), "
    "and rely on Qualtrics, SurveyMonkey, and Google Forms for survey instrument design across "
    "nearly every project. For qualitative coding I have worked with structured manual approaches "
    "in collaborative documents and am ready to ramp on Dedoose or NVivo as needed. I am "
    "comfortable with IRB submission for human subjects work where applicable.",

    "The \"Tribal, state, and local\" framing in the position description is one of the parts I "
    "want to flag specifically. At Seattle Children's Mental Health Referral Service I led "
    "culturally responsive partnership work with Indigenous communities, redesigning regional "
    "outreach to improve service accessibility and cultural appropriateness. That work was part "
    "of a broader 59% reduction in mental health service turnaround time for Medicaid-enrolled "
    "families in our region. Earlier work at Teton Youth and Family Services anchored my "
    "trauma-informed practice, which has carried through everything since.",

    "In my current Seattle Children's role I have been adapting these instincts to enterprise "
    "systems work — leading Workday integration, designing blended learning curricula grounded "
    "in adult learning principles, and orchestrating LMS curricula for a diverse audience of "
    "users. The role has been a useful expansion, but the work I want to be doing for the long "
    "term is the kind Cardea is doing: evaluation that takes equity and community wisdom "
    "seriously as part of the methodology rather than as a frame applied afterward.",

    "I would welcome a conversation about the role and about the current evaluation portfolio. "
    "Thank you for considering my application.",
]

# Styles
def make_styles():
    s = {}
    s['header_name'] = ParagraphStyle('HeaderName', fontName='Helvetica-Bold', fontSize=14,
        leading=18, textColor=INK, spaceAfter=2, alignment=TA_LEFT)
    s['header_meta'] = ParagraphStyle('HeaderMeta', fontName='Helvetica', fontSize=9.5,
        leading=13, textColor=SLATE, spaceAfter=2, alignment=TA_LEFT)
    s['date'] = ParagraphStyle('Date', fontName='Helvetica', fontSize=10, leading=13,
        textColor=SLATE, spaceAfter=10, alignment=TA_LEFT)
    s['salutation'] = ParagraphStyle('Salutation', fontName='Helvetica', fontSize=11,
        leading=14, textColor=GRAPHITE, spaceAfter=6, alignment=TA_LEFT)
    s['body'] = ParagraphStyle('Body', fontName='Helvetica', fontSize=9.5, leading=12.5,
        textColor=GRAPHITE, spaceAfter=5, alignment=TA_JUSTIFY)
    s['signoff'] = ParagraphStyle('Signoff', fontName='Helvetica', fontSize=10.5, leading=13,
        textColor=GRAPHITE, spaceBefore=5, spaceAfter=10, alignment=TA_LEFT)
    s['signature'] = ParagraphStyle('Signature', fontName='Helvetica-Bold', fontSize=11,
        leading=14, textColor=INK, spaceAfter=2, alignment=TA_LEFT)
    s['footer_meta'] = ParagraphStyle('FooterMeta', fontName='Helvetica', fontSize=9,
        leading=12, textColor=SLATE, alignment=TA_LEFT)
    return s

S = make_styles()

def first_page(canvas, doc):
    canvas.saveState()
    # Top accent bar
    canvas.setFillColor(INK)
    canvas.rect(0, letter[1] - 8, letter[0], 8, fill=1, stroke=0)
    # Ochre thin line below
    canvas.setStrokeColor(OCHRE)
    canvas.setLineWidth(1.5)
    canvas.line(72, letter[1] - 12, letter[0] - 72, letter[1] - 12)
    # Bottom ochre rule
    canvas.setLineWidth(0.75)
    canvas.line(72, 48, letter[0] - 72, 48)
    canvas.restoreState()

# Document
doc = SimpleDocTemplate(
    OUTPUT_FILE, pagesize=letter,
    topMargin=0.55*inch, bottomMargin=0.55*inch,
    leftMargin=0.85*inch, rightMargin=0.85*inch,
)
story = []

# Header (sender info, top-left)
story.append(Paragraph(SENDER_NAME, S['header_name']))
story.append(Paragraph(SENDER_TITLE, S['header_meta']))
story.append(Paragraph(SENDER_EMAIL + "  ·  " + SENDER_LOCATION, S['header_meta']))
story.append(Spacer(1, 8))
story.append(HRFlowable(width="100%", thickness=0.5, color=RULE,
                         spaceAfter=8, spaceBefore=2))

# Date
story.append(Paragraph(DATE_LINE, S['date']))

# Salutation
story.append(Paragraph(RECIPIENT, S['salutation']))

# Body
for p in BODY_PARAGRAPHS:
    story.append(Paragraph(p, S['body']))

# Signoff + signature
story.append(Paragraph("Sincerely,", S['signoff']))
story.append(Paragraph(SIGNATURE_NAME, S['signature']))
story.append(Paragraph(SENDER_TITLE, S['header_meta']))
story.append(Paragraph(SENDER_EMAIL, S['header_meta']))

doc.build(story, onFirstPage=first_page, onLaterPages=first_page)
print(f"Cover letter PDF created: {OUTPUT_FILE}")
