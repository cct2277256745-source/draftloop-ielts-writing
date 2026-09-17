"""PDF 报告导出：用 reportlab 生成排版精美的批改报告。

包含：总分 + 四项打分表、考生原文、逐段修改建议、高分重写、
目标 Band 重写、逐句对照修改、Brainstorming Ideas 与高级词伙。
注册 macOS 系统中文字体以支持中英混排。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    CondPageBreak, HRFlowable, KeepTogether, ListFlowable, ListItem, Paragraph,
    SimpleDocTemplate, Spacer, Table, TableStyle,
)

from .models import GradingResult, Replacement

INK = colors.HexColor("#1F1F1F")
MUTED = colors.HexColor("#4A4A4A")
PAPER = colors.HexColor("#FFFEFB")
PAPER_ALT = colors.HexColor("#FAF9F6")
BLUE = colors.HexColor("#EAF0F6")
BLUE_LINE = colors.HexColor("#C9CED4")
BLUE_DARK = colors.HexColor("#19395C")
MIDNIGHT = colors.HexColor("#191970")
MIDNIGHT_25 = colors.HexColor("#C6C6DB")
MIDNIGHT_SOFT = colors.HexColor("#D8D8E7")
YELLOW = colors.HexColor("#FBF4DC")
YELLOW_LINE = colors.HexColor("#D7C6A5")
YELLOW_DARK = colors.HexColor("#8A651D")
RED = colors.HexColor("#F8ECE9")
RED_LINE = colors.HexColor("#D7B6B1")
RED_DARK = colors.HexColor("#8A3B35")
RULE = colors.HexColor("#7D8791")
GREEN = colors.HexColor("#E5F1EB")
GREEN_LINE = colors.HexColor("#8FB9A8")
GREEN_DARK = colors.HexColor("#126B5E")
CREAM = colors.HexColor("#F5EDDA")
CREAM_LINE = colors.HexColor("#D3B778")


class _NumberedCanvas(canvas.Canvas):
    """延迟写页，在页脚绘制与模板一致的 current / total 页码。"""

    def __init__(self, *args, **kwargs):
        canvas.Canvas.__init__(self, *args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.setFont("Times-Roman", 8)
            self.setFillColor(MUTED)
            self.drawString(
                17 * mm,
                A4[1] - 9 * mm,
                "DraftLoop · AI Writing Coach for IELTS",
            )
            self.setStrokeColor(BLUE_LINE)
            self.setLineWidth(0.35)
            self.line(17 * mm, A4[1] - 11 * mm, A4[0] - 17 * mm, A4[1] - 11 * mm)
            self.drawRightString(
                A4[0] - 17 * mm,
                8.5 * mm,
                f"{self._pageNumber:02d} / {total:02d}",
            )
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)

# macOS 常见中文字体候选；后两项是常规与粗体的 TTC 子字库索引。
_CJK_CANDIDATES = [
    ("/System/Library/Fonts/Supplemental/Songti.ttc", 6, 1),
    ("/Library/Fonts/SimSun.ttf", 0, 0),
    ("/Library/Fonts/simsun.ttc", 0, 0),
    (str(Path.home() / "Library/Fonts/SimSun.ttf"), 0, 0),
    (str(Path.home() / "Library/Fonts/simsun.ttc"), 0, 0),
    ("/Library/Fonts/Songti.ttc", 0, 0),
    ("/System/Library/Fonts/STHeiti Medium.ttc", 0, 0),
    ("/System/Library/Fonts/STHeiti Light.ttc", 0, 0),
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", 0, 0),
    ("/System/Library/Fonts/PingFang.ttc", 0, 0),
]


def _register_cjk_font() -> str:
    """注册一个可用的中文字体，返回字体名；失败则回退 Helvetica。"""
    for path, regular_index, bold_index in _CJK_CANDIDATES:
        if Path(path).exists():
            try:
                pdfmetrics.registerFont(
                    TTFont("CJK", path, subfontIndex=regular_index)
                )
                pdfmetrics.registerFont(
                    TTFont("CJK-Bold", path, subfontIndex=bold_index)
                )
                pdfmetrics.registerFontFamily(
                    "CJK",
                    normal="CJK",
                    bold="CJK-Bold",
                    italic="CJK",
                    boldItalic="CJK-Bold",
                )
                return "CJK"
            except Exception:
                continue
    return "Helvetica"


def _register_english_font() -> str:
    family = {
        "normal": "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
        "bold": "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",
        "italic": "/System/Library/Fonts/Supplemental/Times New Roman Italic.ttf",
        "boldItalic": "/System/Library/Fonts/Supplemental/Times New Roman Bold Italic.ttf",
    }
    if not all(Path(path).exists() for path in family.values()):
        return "Times-Roman"
    try:
        names = {
            "normal": "TimesNewRoman",
            "bold": "TimesNewRoman-Bold",
            "italic": "TimesNewRoman-Italic",
            "boldItalic": "TimesNewRoman-BoldItalic",
        }
        for key, path in family.items():
            pdfmetrics.registerFont(TTFont(names[key], path))
        pdfmetrics.registerFontFamily(
            "TimesNewRoman",
            normal=names["normal"],
            bold=names["bold"],
            italic=names["italic"],
            boldItalic=names["boldItalic"],
        )
        return "TimesNewRoman"
    except Exception:
        return "Times-Roman"


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _labelled_text(label: str, text: str, styles) -> str:
    cjk_font = styles["body"].fontName
    english_font = styles["englishBody"].fontName
    return (
        f'<font name="{cjk_font}"><b>{_escape(label)}</b></font><br/>'
        f'<font name="{english_font}">{_escape(text)}</font>'
    )


def export_report(
    path: str,
    task_type: str,
    question: str,
    essay: str,
    result: GradingResult,
) -> None:
    font = _register_cjk_font()
    english_font = _register_english_font()
    doc = SimpleDocTemplate(
        path, pagesize=A4,
        topMargin=15 * mm, bottomMargin=14 * mm,
        leftMargin=17 * mm, rightMargin=17 * mm,
        title="雅思写作批改报告",
    )
    styles = _build_styles(font, english_font)
    story: list = []

    _add_header(story, styles, task_type, result)
    _add_question_and_essay(story, styles, question, essay)
    _add_scores(story, styles, result, english_font)
    _add_criterion_analysis(story, styles, result)
    _add_score_diagnosis(story, styles, result)
    _add_essay_mind_map(story, styles, result)
    _add_task1_checks(story, styles, result)
    _add_paragraph_feedback(story, styles, result)
    _add_vocabulary_upgrades(story, styles, result)
    _add_syntax_upgrades(story, styles, result)
    _add_pipeline_versions(story, styles, result)
    _add_rewrite(story, styles, result)
    _add_target_rewrites(story, styles, result)
    _add_sentence_comparisons(story, styles, result)
    _add_final_learning_items(story, styles, result)
    _add_ideas_and_collocations(story, styles, result)
    _add_final_takeaway(story, styles, result)

    doc.build(story, canvasmaker=_NumberedCanvas)


def _build_styles(font: str, english_font: str) -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    s: dict[str, ParagraphStyle] = {}
    s["kicker"] = ParagraphStyle(
        "kicker", parent=base["Normal"], fontName=english_font,
        fontSize=8.5, leading=10, textColor=BLUE_DARK,
        spaceAfter=7, tracking=1.8,
    )
    s["title"] = ParagraphStyle(
        "title", parent=base["Normal"], fontName=font, fontSize=28,
        textColor=INK, spaceAfter=6, leading=30,
    )
    s["meta"] = ParagraphStyle(
        "meta", parent=base["Normal"], fontName=font, fontSize=10,
        textColor=MUTED, spaceAfter=14, leading=14,
    )
    s["h2"] = ParagraphStyle(
        "h2", parent=base["Normal"], fontName=font, fontSize=16,
        textColor=INK, spaceBefore=8, spaceAfter=5, leading=20,
    )
    s["h3"] = ParagraphStyle(
        "h3", parent=base["Normal"], fontName=font, fontSize=11.5,
        textColor=INK, spaceBefore=8, spaceAfter=5, leading=15,
    )
    s["body"] = ParagraphStyle(
        "body", parent=base["Normal"], fontName=font, fontSize=9.7,
        textColor=INK, leading=15, alignment=TA_LEFT, spaceAfter=4,
    )
    s["englishBody"] = ParagraphStyle(
        "englishBody", parent=s["body"], fontName=english_font, fontSize=10.2,
        leading=15.5,
    )
    s["essay"] = ParagraphStyle(
        "essay", parent=s["englishBody"], fontSize=10.7, leading=17,
        spaceAfter=8,
    )
    s["finalEssay"] = ParagraphStyle(
        "finalEssay", parent=s["englishBody"], fontSize=12.5, leading=20,
        textColor=INK, spaceAfter=0,
    )
    s["finalMeta"] = ParagraphStyle(
        "finalMeta", parent=s["body"], fontSize=8.5, leading=12,
        textColor=MIDNIGHT, spaceAfter=7,
    )
    s["small"] = ParagraphStyle(
        "small", parent=base["Normal"], fontName=font, fontSize=8.5,
        textColor=MUTED, leading=12,
    )
    s["bullet"] = ParagraphStyle(
        "bullet", parent=s["body"], fontSize=9.5, leading=13, spaceAfter=1,
    )
    s["sectionIndex"] = ParagraphStyle(
        "sectionIndex", parent=base["Normal"], fontName=english_font,
        fontSize=9.5, leading=12, textColor=YELLOW_DARK,
        tracking=1.2, spaceAfter=0,
    )
    s["sectionSquares"] = ParagraphStyle(
        "sectionSquares", parent=base["Normal"], fontName=font,
        fontSize=8.5, leading=10, textColor=colors.HexColor("#9BA7AD"),
        tracking=1.4, spaceAfter=0,
    )
    s["sectionEnglish"] = ParagraphStyle(
        "sectionEnglish", parent=base["Normal"], fontName=english_font,
        fontSize=7.2, leading=9, textColor=MUTED, alignment=2,
        tracking=0.9, spaceAfter=0,
    )
    s["sectionTitle"] = ParagraphStyle(
        "sectionTitle", parent=s["h2"], fontSize=17,
        leading=21, spaceBefore=0, spaceAfter=1,
    )
    s["sectionNote"] = ParagraphStyle(
        "sectionNote", parent=s["body"], fontSize=8.8,
        leading=12, textColor=MUTED, spaceAfter=0,
    )
    s["microLabel"] = ParagraphStyle(
        "microLabel", parent=s["body"], fontSize=8,
        leading=10, textColor=MUTED, spaceAfter=3,
    )
    s["criterionName"] = ParagraphStyle(
        "criterionName", parent=s["body"], fontSize=9.2,
        leading=12, textColor=BLUE_DARK, spaceAfter=0,
    )
    s["criterionBody"] = ParagraphStyle(
        "criterionBody", parent=s["body"], fontSize=8.8,
        leading=13, spaceAfter=0,
    )
    s["commentaryLabel"] = ParagraphStyle(
        "commentaryLabel", parent=s["body"], fontSize=8.8,
        leading=11, textColor=BLUE_DARK, spaceAfter=4,
    )
    s["commentaryText"] = ParagraphStyle(
        "commentaryText", parent=s["body"], fontSize=9.4,
        leading=15, spaceAfter=0,
    )
    s["scoreNote"] = ParagraphStyle(
        "scoreNote", parent=s["body"], fontSize=8.8, leading=12.5,
        textColor=GREEN_DARK, spaceAfter=0,
    )
    s["scoreCaption"] = ParagraphStyle(
        "scoreCaption", parent=s["small"], fontSize=7.4, leading=9,
        textColor=MUTED, spaceAfter=0,
    )
    s["scoreValue"] = ParagraphStyle(
        "scoreValue", parent=base["Normal"], fontName=english_font,
        fontSize=21, leading=25, textColor=BLUE_DARK, spaceAfter=2,
    )
    s["scoreValueOverall"] = ParagraphStyle(
        "scoreValueOverall", parent=s["scoreValue"], fontSize=27,
        leading=31,
    )
    s["paragraphIndex"] = ParagraphStyle(
        "paragraphIndex", parent=base["Normal"], fontName=english_font,
        fontSize=8, leading=10, textColor=colors.white, spaceAfter=0,
    )
    s["mapThesis"] = ParagraphStyle(
        "mapThesis", parent=s["body"], fontSize=10, leading=15,
        textColor=BLUE_DARK, spaceAfter=0,
    )
    s["cardLabel"] = ParagraphStyle(
        "cardLabel", parent=s["body"], fontSize=8.1, leading=10,
        textColor=BLUE_DARK, spaceAfter=3,
    )
    section_specs = {
        "sectionBlue": (PAPER, BLUE_DARK),
        "sectionNeutral": (PAPER, RULE),
        "sectionYellow": (YELLOW, YELLOW_LINE),
        "sectionRed": (RED, RED_LINE),
    }
    for name, (background, border) in section_specs.items():
        s[name] = ParagraphStyle(
            name, parent=s["h2"], backColor=background, borderColor=border,
            borderWidth=0.8, borderPadding=7, spaceBefore=10, spaceAfter=7,
        )
    card_specs = {
        "diagnosisCard": (BLUE, BLUE_LINE),
        "neutralCard": (PAPER, BLUE_LINE),
        "feedbackCard": (PAPER, BLUE_LINE),
        "blueCard": (BLUE, BLUE_LINE),
        "yellowCard": (YELLOW, YELLOW_LINE),
        "redCard": (RED, RED_LINE),
        "decisionCard": (PAPER, BLUE_LINE),
        "memoryCard": (PAPER, BLUE_LINE),
        "practiceCard": (YELLOW, YELLOW_LINE),
    }
    for name, (background, border) in card_specs.items():
        s[name] = ParagraphStyle(
            name, parent=s["body"], backColor=background, borderColor=border,
            borderWidth=0.6, borderPadding=8, leading=14, spaceAfter=7,
        )
    s["tableCell"] = ParagraphStyle(
        "tableCell", parent=s["englishBody"], fontSize=8.8,
        leading=12.5, spaceAfter=0,
    )
    s["tableBody"] = ParagraphStyle(
        "tableBody", parent=s["body"], fontSize=8.7,
        leading=12.5, spaceAfter=0,
    )
    s["tableEnglish"] = ParagraphStyle(
        "tableEnglish", parent=s["englishBody"], fontSize=8.9,
        leading=13, spaceAfter=0,
    )
    s["tableEnglishBlue"] = ParagraphStyle(
        "tableEnglishBlue", parent=s["tableEnglish"],
        textColor=MIDNIGHT,
    )
    s["tableHead"] = ParagraphStyle(
        "tableHead", parent=s["body"], fontSize=8.5, leading=10.5,
        textColor=colors.white, spaceAfter=0,
    )
    s["blueTitle"] = ParagraphStyle(
        "blueTitle", parent=s["h3"], textColor=BLUE_DARK,
        spaceBefore=5, spaceAfter=5,
    )
    s["yellowTitle"] = ParagraphStyle(
        "yellowTitle", parent=s["h3"], textColor=YELLOW_DARK,
        backColor=YELLOW, borderColor=YELLOW_LINE, borderWidth=0.6,
        borderPadding=6, spaceBefore=5, spaceAfter=5,
    )
    s["redTitle"] = ParagraphStyle(
        "redTitle", parent=s["h3"], textColor=RED_DARK,
        backColor=RED, borderColor=RED_LINE, borderWidth=0.6,
        borderPadding=6, spaceBefore=5, spaceAfter=5,
    )
    s["finalSection"] = ParagraphStyle(
        "finalSection", parent=s["sectionTitle"], textColor=MIDNIGHT,
    )
    return s


def _section_heading(
    story,
    styles,
    index: str,
    title: str,
    note: str = "",
) -> None:
    section_english = {
        "00": "ORIGINAL ESSAY",
        "01": "SCORE OVERVIEW",
        "02": "FOUR CRITERIA ANALYSIS",
        "03": "STRENGTH & MAIN BOTTLENECK",
        "04": "ESSAY MIND MAP",
        "05": "PARAGRAPH-BY-PARAGRAPH REVIEW",
        "06": "ONLY MEANINGFUL CHANGES",
        "07": "MINIMAL FULL-ESSAY VERSION",
        "08": "TOPIC KNOWLEDGE PACK",
        "09": "WHAT TO KEEP & WHAT TO TRAIN",
        "10": "FINAL TAKEAWAY",
    }.get(index, "DRAFTLOOP REPORT")
    content = [Paragraph(title, styles["sectionTitle"])]
    if note:
        content.append(Paragraph(note, styles["sectionNote"]))
    heading = Table(
        [[
            Paragraph(index, styles["sectionIndex"]),
            Paragraph("□□□□□□", styles["sectionSquares"]),
            content,
            Paragraph(section_english, styles["sectionEnglish"]),
        ]],
        colWidths=[12 * mm, 25 * mm, 94 * mm, 43 * mm],
        hAlign="LEFT",
    )
    heading.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEABOVE", (0, 0), (-1, 0), 0.75, BLUE_DARK),
        ("LINEBELOW", (0, 0), (-1, 0), 0.4, BLUE_LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(heading)
    story.append(Spacer(1, 9))


def _add_header(story, styles, task_type, result) -> None:
    label = "Academic Task 1" if task_type == "task1" else "Task 2"
    story.append(Paragraph(
        "DraftLoop · AI Writing Coach for IELTS",
        styles["kicker"],
    ))
    story.append(Paragraph("深度总报告 · IELTS Writing", styles["title"]))
    story.append(Paragraph(
        f"{label}　·　FINAL DETAILED REPORT　·　Target Band {result.scoreDiagnosis.targetBand:g}"
        f"　·　生成日期 {date.today():%Y-%m-%d}",
        styles["meta"],
    ))
    rule = Table([["", ""]], colWidths=[60 * mm, 114 * mm], rowHeights=[2])
    rule.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), BLUE_DARK),
        ("BACKGROUND", (1, 0), (1, 0), BLUE_LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(rule)
    story.append(Spacer(1, 15))


def _add_scores(story, styles, result: GradingResult, font: str) -> None:
    _section_heading(
        story,
        styles,
        "01",
        "Score Diagnosis 分数诊断",
        "完整考官评语与四项评分依据",
    )

    first_label = "TA" if result.taskType == "task1" else "TR"
    by_label = {sc.label: sc for sc in result.scores}
    scores = [result.overallBand]
    for lbl in [first_label, "CC", "LR", "GRA"]:
        sc = by_label.get(lbl) or (by_label.get("TR/TA") if lbl == first_label else None)
        scores.append(sc.score if sc else None)
    labels = [
        ("OVERALL", "Current band"),
        (first_label, "Task Achievement" if first_label == "TA" else "Task Response"),
        ("CC", "Coherence"),
        ("LR", "Lexical Resource"),
        ("GRA", "Grammar"),
    ]
    cells = []
    for index, ((label, caption), score) in enumerate(zip(labels, scores)):
        value = "—" if score is None else f"{score:.1f}"
        foreground = BLUE_DARK
        caption_color = YELLOW_DARK if index == 0 else MUTED
        cells.append([
            Paragraph(
                f'<font color="{caption_color.hexval()}"><b>{label}</b></font>',
                styles["microLabel"],
            ),
            Paragraph(
                f"<b>{value}</b>",
                styles["scoreValueOverall" if index == 0 else "scoreValue"],
            ),
            Paragraph(
                f'<font color="{caption_color.hexval()}">{caption}</font>',
                styles["small"],
            ),
        ])

    table = Table([cells], colWidths=[43 * mm] + [32.75 * mm] * 4)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), CREAM),
        ("BACKGROUND", (1, 0), (-1, 0), PAPER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOX", (0, 0), (-1, 0), 0.8, BLUE_DARK),
        ("INNERGRID", (0, 0), (-1, 0), 0.5, BLUE_LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    story.append(table)
    diagnosis = result.scoreDiagnosis
    priority = ""
    if diagnosis.criteria:
        weakest = min(diagnosis.criteria, key=lambda item: item.score)
        priority = weakest.name
    context = (
        f"Target <b>{diagnosis.targetBand:g}</b>　　"
        f"Gap <b>{diagnosis.gapToTarget:g}</b>"
    )
    if priority:
        context += f"　　Priority <b>{_escape(priority)}</b>"
    story.append(Table(
        [["", Paragraph(context, styles["scoreNote"])]],
        colWidths=[2 * mm, 172 * mm],
        style=TableStyle([
            ("BACKGROUND", (0, 0), (0, 0), GREEN_DARK),
            ("BACKGROUND", (1, 0), (1, 0), GREEN),
            ("LEFTPADDING", (0, 0), (0, 0), 0),
            ("RIGHTPADDING", (0, 0), (0, 0), 0),
            ("LEFTPADDING", (1, 0), (1, 0), 8),
            ("RIGHTPADDING", (1, 0), (1, 0), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]),
    ))


def _add_criterion_analysis(story, styles, result: GradingResult) -> None:
    criteria = result.scoreDiagnosis.criteria
    if not criteria:
        return
    story.append(CondPageBreak(48 * mm))
    _section_heading(
        story,
        styles,
        "02",
        "Four Criteria Analysis 四项评分",
        "每项评分的完成度、限制因素与下一步动作",
    )
    for item in criteria:
        score = f"{item.score:.1f}"
        title = Table(
            [[
                Paragraph(
                    f"<b>{_escape(item.name or 'Criterion')}</b>",
                    styles["h3"],
                ),
                Paragraph(f"<b>{score}</b>", styles["h3"]),
            ]],
            colWidths=[150 * mm, 24 * mm],
            hAlign="LEFT",
        )
        title.setStyle(TableStyle([
            ("ALIGN", (1, 0), (1, 0), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LINEABOVE", (0, 0), (-1, 0), 0.6, BLUE_LINE),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        analysis = item.commentZh or item.commentEn or "当前证据不足以单独补充更多说明。"
        strengths = item.commentZh or item.commentEn or "继续保持当前完成度。"
        limits = item.mainProblemsZh or item.mainProblemsEn
        next_step = item.nextStepZh or item.nextStepEn
        limit_text = "；".join(limits) if limits else (next_step or "当前证据没有显示需要单独指出的限制因素。")
        body = Table(
            [[
                Paragraph(
                    f"<b>评分依据</b><br/>{_escape(analysis)}<br/><br/>"
                    f"<b>做得好的地方</b><br/>{_escape(strengths)}",
                    styles["tableBody"],
                ),
                Paragraph(
                    f"<b>限制这一项的因素</b><br/>{_escape(limit_text)}",
                    styles["tableBody"],
                ),
            ]],
            colWidths=[87 * mm, 87 * mm],
            hAlign="LEFT",
        )
        body.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, 0), GREEN),
            ("BACKGROUND", (1, 0), (1, 0), CREAM),
            ("LINEBELOW", (0, 0), (-1, 0), 0.45, BLUE_LINE),
            ("INNERGRID", (0, 0), (-1, -1), 0.4, BLUE_LINE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(KeepTogether([title, body, Spacer(1, 7)]))


def _add_score_diagnosis(story, styles, result: GradingResult) -> None:
    diagnosis = result.scoreDiagnosis
    if not (diagnosis.currentLevelSummaryEn or diagnosis.currentLevelSummaryZh):
        return
    commentary = [
        text for text in (
            diagnosis.currentLevelSummaryEn,
            diagnosis.currentLevelSummaryZh,
            diagnosis.whyThisScoreEn,
            diagnosis.whyThisScoreZh,
            diagnosis.targetGapAnalysisEn,
            diagnosis.targetGapAnalysisZh,
    ) if text
    ]
    if commentary:
        _section_heading(
            story,
            styles,
            "03",
            "Strength & Main Bottleneck 强项与主要瓶颈",
            "把最值得保留的能力与最值得训练的差距分开看",
        )
        story.append(Paragraph(
            "<b>Examiner Commentary 考官完整评语</b>",
            styles["commentaryLabel"],
        ))
        for text in commentary:
            block = Table(
                [["", Paragraph(_escape(text), styles["commentaryText"])]],
                colWidths=[1.5 * mm, 172.5 * mm],
                hAlign="LEFT",
            )
            block.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (0, 0), BLUE_DARK),
                ("BACKGROUND", (1, 0), (1, 0), BLUE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (0, 0), 0),
                ("RIGHTPADDING", (0, 0), (0, 0), 0),
                ("LEFTPADDING", (1, 0), (1, 0), 9),
                ("RIGHTPADDING", (1, 0), (1, 0), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            story.append(block)
        story.append(Spacer(1, 9))


def _add_essay_mind_map(story, styles, result: GradingResult) -> None:
    if not result.paragraphFeedback:
        return
    _section_heading(
        story,
        styles,
        "04",
        "Essay Mind Map 文章思路",
        "把段落功能、论点支撑和最需要补强的位置放在同一张图里",
    )
    if result.summary.strip():
        thesis = Table(
            [["", Paragraph(
                f"<b>中心立场 / CORE POSITION</b><br/>{_escape(result.summary)}",
                styles["mapThesis"],
            )]],
            colWidths=[2 * mm, 172 * mm],
            hAlign="LEFT",
        )
        thesis.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, 0), BLUE_DARK),
            ("BACKGROUND", (1, 0), (1, 0), BLUE),
            ("LEFTPADDING", (0, 0), (0, 0), 0),
            ("RIGHTPADDING", (0, 0), (0, 0), 0),
            ("LEFTPADDING", (1, 0), (1, 0), 9),
            ("RIGHTPADDING", (1, 0), (1, 0), 9),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(thesis)
        story.append(Spacer(1, 8))
    rows = [[
        Paragraph("段落", styles["tableHead"]),
        Paragraph("功能与论点", styles["tableHead"]),
        Paragraph("支撑与待补全", styles["tableHead"]),
    ]]
    for item in result.paragraphFeedback:
        function = item.function or "段落功能待明确"
        strength = item.strengthsZh[0] if item.strengthsZh else (item.strengthsEn[0] if item.strengthsEn else "当前证据未单独列出优势")
        issue = item.issuesZh[0] if item.issuesZh else (item.issuesEn[0] if item.issuesEn else "当前证据未单独列出限制因素")
        rows.append([
            Paragraph(f"<b>P{item.paragraph}</b>", styles["tableBody"]),
            Paragraph(f"<b>{_escape(function)}</b><br/>{_escape(strength)}", styles["tableBody"]),
            Paragraph(f"<b>待补全</b><br/>{_escape(issue)}", styles["tableBody"]),
        ])
    map_table = Table(
        rows,
        colWidths=[20 * mm, 74 * mm, 80 * mm],
        repeatRows=1,
        splitByRow=1,
        hAlign="LEFT",
    )
    map_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLUE_DARK),
        ("BACKGROUND", (0, 1), (0, -1), CREAM),
        ("BACKGROUND", (1, 1), (1, -1), GREEN),
        ("BACKGROUND", (2, 1), (2, -1), PAPER),
        ("INNERGRID", (0, 0), (-1, -1), 0.35, BLUE_LINE),
        ("BOX", (0, 0), (-1, -1), 0.6, BLUE_LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(map_table)


def _para_block(text: str, styles, numbered: bool = False) -> list:
    """把含空行的多段文本拆成多个 Paragraph，保留段落间距。"""
    out = []
    chunks = [chunk.strip() for chunk in text.split("\n\n") if chunk.strip()]
    if len(chunks) <= 1:
        chunks = [chunk.strip() for chunk in text.splitlines() if chunk.strip()]
    for index, chunk in enumerate(chunks, 1):
        chunk = chunk.strip()
        if chunk:
            paragraph = Paragraph(
                _escape(chunk).replace("\n", "<br/>"),
                styles["essay"],
            )
            if numbered:
                row = Table(
                    [[Paragraph(f"<b>P{index}</b>", styles["paragraphIndex"]), paragraph]],
                    colWidths=[12 * mm, 162 * mm],
                    hAlign="LEFT",
                )
                row.setStyle(TableStyle([
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("BACKGROUND", (0, 0), (0, 0), BLUE_DARK),
                    ("BACKGROUND", (1, 0), (1, 0), BLUE),
                    ("TEXTCOLOR", (0, 0), (0, 0), colors.white),
                    ("LINEBEFORE", (0, 0), (0, 0), 1.2, YELLOW_DARK),
                    ("BOX", (0, 0), (-1, -1), 0.35, BLUE_LINE),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (0, 0), 6),
                    ("RIGHTPADDING", (1, 0), (1, 0), 0),
                    ("LEFTPADDING", (1, 0), (1, 0), 9),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ]))
                out.append(row)
            else:
                out.append(paragraph)
    return out


def _add_question_and_essay(story, styles, question: str, essay: str) -> None:
    story.append(CondPageBreak(58 * mm))
    _section_heading(
        story,
        styles,
        "00",
        "Original Essay 原文与题目",
        "先保留证据，再进入评分与修改",
    )
    question_table = Table(
        [[Paragraph(
            "<b>QUESTION 题目</b><br/><br/>"
            f'<font name="{styles["englishBody"].fontName}" size="11">'
            f'{_escape(question)}</font>',
            styles["body"],
        )]],
        colWidths=[174 * mm],
        hAlign="LEFT",
    )
    question_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), PAPER_ALT),
        ("LINEABOVE", (0, 0), (0, 0), 0.7, RULE),
        ("LINEBELOW", (0, 0), (0, 0), 0.7, RULE),
        ("LEFTPADDING", (0, 0), (0, 0), 8),
        ("RIGHTPADDING", (0, 0), (0, 0), 8),
        ("TOPPADDING", (0, 0), (0, 0), 8),
        ("BOTTOMPADDING", (0, 0), (0, 0), 8),
    ]))
    story.append(question_table)
    story.append(Spacer(1, 12))
    essay_blocks = _para_block(essay, styles, numbered=True)
    if essay_blocks:
        story.extend(essay_blocks)
        story.append(Spacer(1, 6))
        story.append(HRFlowable(
            width="100%", thickness=0.7, color=BLUE_LINE,
            spaceBefore=3, spaceAfter=8,
        ))


def _add_task1_checks(story, styles, result: GradingResult) -> None:
    if result.taskType != "task1":
        return
    for title, data in (
        ("Chart Understanding 图表理解", result.chartUnderstanding),
        ("Data Accuracy Check 数据准确性", result.dataAccuracyCheck),
        ("Overview Check 概述检查", result.overviewCheck),
    ):
        if not data:
            continue
        story.append(Paragraph(title, styles["sectionBlue"]))
        items = []
        for key, value in data.items():
            rendered = "; ".join(str(x) for x in value) if isinstance(value, list) else str(value)
            items.append(f"{key}: {rendered}")
        story.append(Paragraph(
            "<br/>".join(f"• {_escape(item)}" for item in items),
            styles["blueCard"],
        ))


def _add_pipeline_versions(story, styles, result: GradingResult) -> None:
    text = result.balancedFinalVersion.strip()
    if not text:
        return
    story.append(CondPageBreak(72 * mm))
    _section_heading(
        story,
        styles,
        "07",
        "Minimal Full-Essay Version 最小改进全文",
        "只做必要修改，保留考生原有立场与论证顺序",
    )
    intro = Table(
        [[
            [
                Paragraph("FINAL LEARNING VERSION", styles["kicker"]),
                Paragraph(
                    "Final Revised Essay 最终复核版范文",
                    styles["finalSection"],
                ),
            ],
            Paragraph("RECOMMENDED", styles["kicker"]),
        ]],
        colWidths=[139 * mm, 35 * mm],
        hAlign="LEFT",
    )
    intro.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(intro)
    story.append(HRFlowable(
        width="100%", thickness=2, color=MIDNIGHT,
        spaceBefore=8, spaceAfter=13,
    ))
    for chunk in (part.strip() for part in text.split("\n\n")):
        if not chunk:
            continue
        essay_row = Table(
            [["", Paragraph(
                _escape(chunk).replace("\n", "<br/>"),
                styles["finalEssay"],
            )]],
            colWidths=[2 * mm, 172 * mm],
            hAlign="LEFT",
        )
        essay_row.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, 0), MIDNIGHT),
            ("BACKGROUND", (1, 0), (1, 0), MIDNIGHT_SOFT),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (0, 0), 0),
            ("RIGHTPADDING", (0, 0), (0, 0), 0),
            ("LEFTPADDING", (1, 0), (1, 0), 10),
            ("RIGHTPADDING", (1, 0), (1, 0), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(essay_row)
    story.append(HRFlowable(
        width="100%", thickness=0.5, color=BLUE_LINE,
        spaceBefore=13, spaceAfter=7,
    ))
    story.append(Paragraph(
        "该版本是最终推荐学习稿：保留原始立场，融合自然表达与可控句法，并经过最终复核。",
        styles["sectionNote"],
    ))

def _add_vocabulary_upgrades(story, styles, result: GradingResult) -> None:
    if not result.vocabularyUpgrades:
        return
    story.append(CondPageBreak(62 * mm))
    _section_heading(
        story,
        styles,
        "06",
        "Only Meaningful Changes 只列出有意义的修改",
        "词汇、语法与句式修改都保留原文证据",
    )
    story.append(Paragraph(
        "Vocabulary Upgrade 词汇升级",
        styles["h3"],
    ))
    rows = [[
        Paragraph("<b>Original<br/>原表达</b>", styles["tableHead"]),
        Paragraph("<b>Suggested<br/>建议表达</b>", styles["tableHead"]),
        Paragraph("<b>Why<br/>为什么更好</b>", styles["tableHead"]),
        Paragraph("<b>Recommendation<br/>建议使用方式</b>", styles["tableHead"]),
    ]]
    for item in result.vocabularyUpgrades:
        rows.append([
            Paragraph(_escape(item.originalExpression or "—"), styles["tableCell"]),
            Paragraph(_escape(item.suggestedExpression or "—"), styles["tableCell"]),
            Paragraph(
                _escape(item.whyBetterEn or item.whyBetterZh or "—"),
                styles["tableCell"],
            ),
            Paragraph(_escape(item.recommendation or "—"), styles["tableCell"]),
        ])
    table = Table(
        rows,
        colWidths=[31 * mm, 39 * mm, 65 * mm, 39 * mm],
        repeatRows=1,
        splitByRow=1,
        hAlign="LEFT",
    )
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLUE_DARK),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 1), (0, -1), PAPER),
        ("BACKGROUND", (1, 1), (1, -1), BLUE),
        ("TEXTCOLOR", (1, 1), (1, -1), BLUE_DARK),
        ("BACKGROUND", (2, 1), (2, -1), PAPER),
        ("LINEBELOW", (0, 0), (-1, -1), 0.45, BLUE_LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.35, BLUE_LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    recommendation_style = []
    for row_index, item in enumerate(result.vocabularyUpgrades, 1):
        value = item.recommendation.strip().lower()
        if "avoid" in value:
            background, foreground = RED, RED_DARK
        elif "careful" in value:
            background, foreground = YELLOW, YELLOW_DARK
        else:
            background, foreground = BLUE, BLUE_DARK
        recommendation_style.extend([
            ("BACKGROUND", (3, row_index), (3, row_index), background),
            ("TEXTCOLOR", (3, row_index), (3, row_index), foreground),
        ])
    table.setStyle(TableStyle(recommendation_style))
    story.append(table)


def _add_syntax_upgrades(story, styles, result: GradingResult) -> None:
    if not result.syntaxUpgrades:
        return
    if not result.vocabularyUpgrades:
        story.append(CondPageBreak(62 * mm))
        _section_heading(
            story,
            styles,
            "06",
            "Only Meaningful Changes 只列出有意义的修改",
            "词汇、语法与句式修改都保留原文证据",
        )
    story.append(Paragraph(
        "Syntax Upgrade 可迁移句法",
        styles["h3"],
    ))
    story.append(Spacer(1, 4))
    cards = []
    for item in result.syntaxUpgrades:
        card = [
            Paragraph(
                f"<b>{_escape(item.learningTitleZh)}</b>",
                styles["blueTitle"],
            ),
            Paragraph(
                _labelled_text(
                    "Original Sentence 原句",
                    item.studentOriginalSentence,
                    styles,
                ),
                styles["tableEnglish"],
            ),
            HRFlowable(width="100%", thickness=0.45, color=BLUE_LINE),
            Paragraph(
                _labelled_text(
                    "Final Upgraded Sentence 最终升级句",
                    item.upgradedSentence,
                    styles,
                ),
                styles["tableEnglishBlue"],
            ),
        ]
        if item.learningReasonZh:
            card.extend([
                HRFlowable(width="100%", thickness=0.45, color=BLUE_LINE),
                Paragraph(
                f"<b>为什么值得学</b><br/>{_escape(item.learningReasonZh)}",
                styles["criterionBody"],
                ),
            ])
        if item.howToReuseZh:
            transfer = Table(
                [[Paragraph(
                    f"<b>迁移方法</b><br/>{_escape(item.howToReuseZh)}",
                    styles["criterionBody"],
                )]],
                colWidths=[82 * mm],
                hAlign="LEFT",
            )
            transfer.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (0, 0), YELLOW),
                ("BOX", (0, 0), (0, 0), 0.5, YELLOW_LINE),
                ("VALIGN", (0, 0), (0, 0), "TOP"),
                ("LEFTPADDING", (0, 0), (0, 0), 7),
                ("RIGHTPADDING", (0, 0), (0, 0), 7),
                ("TOPPADDING", (0, 0), (0, 0), 7),
                ("BOTTOMPADDING", (0, 0), (0, 0), 7),
            ]))
            card.append(transfer)
        cards.append(card)
    for index in range(0, len(cards), 2):
        row = [cards[index]]
        if index + 1 < len(cards):
            row.append(cards[index + 1])
        else:
            row.append("")
        table = Table(
            [row],
            colWidths=[87 * mm, 87 * mm],
            hAlign="LEFT",
        )
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEABOVE", (0, 0), (-1, 0), 1.5, BLUE_DARK),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (0, -1), 5),
            ("LEFTPADDING", (1, 0), (1, -1), 5),
            ("RIGHTPADDING", (1, 0), (1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ]))
        story.append(table)


def _add_final_learning_items(story, styles, result: GradingResult) -> None:
    if not (
        result.memoriseWorthyExpressions
        or result.nextPracticeSuggestions
    ):
        return
    story.append(CondPageBreak(58 * mm))
    _section_heading(
        story,
        styles,
        "08",
        "Topic Knowledge Pack 主题积累",
        "从一篇作文沉淀出可以迁移到下一道题的主题素材",
    )

    memory_items = result.memoriseWorthyExpressions[:8]
    practice_items = result.nextPracticeSuggestions[:3]
    if not (memory_items or practice_items):
        return
    story.append(Spacer(1, 13))
    compact_two_column = (
        len(memory_items) <= 4
        and all(
            len(
                item.expression
                + item.meaningZh
                + item.exampleSentence
                + item.whyUsefulZh
                + item.warningZh
            ) < 650
            for item in memory_items
        )
    )
    if compact_two_column:
        rows = [[
            Paragraph(
                "Memorise-worthy Expressions 值得背表达",
                styles["h3"],
            ),
            Paragraph(
                "Next Practice Suggestions 下次训练建议",
                styles["h3"],
            ),
        ]]
        row_count = max(len(memory_items), len(practice_items))
        for index in range(row_count):
            if index < len(memory_items):
                item = memory_items[index]
                details = [text for text in (
                    item.meaningZh,
                    item.exampleSentence,
                    item.whyUsefulZh,
                ) if text]
                memory_body = (
                    f'<font color="#19395C"><b>{_escape(item.expression)}</b></font>'
                )
                if details:
                    memory_body += "<br/>" + "<br/>".join(
                        _escape(text) for text in details
                    )
                if item.warningZh:
                    memory_body += f"<br/><b>注意：</b>{_escape(item.warningZh)}"
                memory = Paragraph(memory_body, styles["criterionBody"])
            else:
                memory = ""
            if index < len(practice_items):
                practice = Paragraph(
                    f'<font color="#19395C"><b>{index + 1:02d}</b></font>　'
                    f"{_escape(practice_items[index])}",
                    styles["criterionBody"],
                )
            else:
                practice = ""
            rows.append([memory, practice])
        learning = Table(
            rows,
            colWidths=[96 * mm, 78 * mm],
            hAlign="LEFT",
            splitByRow=1,
        )
        learning.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEABOVE", (0, 0), (-1, 0), 1.6, INK),
            ("LINEBELOW", (0, 1), (-1, -1), 0.45, BLUE_LINE),
            ("LEFTPADDING", (0, 0), (0, -1), 0),
            ("RIGHTPADDING", (0, 0), (0, -1), 10),
            ("LEFTPADDING", (1, 0), (1, -1), 10),
            ("RIGHTPADDING", (1, 0), (1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]))
        story.append(learning)
    else:
        if memory_items:
            story.append(HRFlowable(
                width="100%", thickness=1.6, color=INK,
                spaceAfter=6,
            ))
            story.append(Paragraph(
                "Memorise-worthy Expressions 值得背表达",
                styles["h3"],
            ))
            for item in memory_items:
                story.append(CondPageBreak(25 * mm))
                details = [text for text in (
                    item.meaningZh,
                    item.exampleSentence,
                    item.whyUsefulZh,
                ) if text]
                body = (
                    f'<font color="#19395C"><b>{_escape(item.expression)}</b></font>'
                )
                if details:
                    body += "<br/>" + "<br/>".join(
                        _escape(text) for text in details
                    )
                if item.warningZh:
                    body += f"<br/><b>注意：</b>{_escape(item.warningZh)}"
                story.append(Paragraph(body, styles["criterionBody"]))
                story.append(HRFlowable(
                    width="100%", thickness=0.45, color=BLUE_LINE,
                    spaceBefore=6, spaceAfter=6,
                ))
        if practice_items:
            story.append(CondPageBreak(35 * mm))
            story.append(HRFlowable(
                width="100%", thickness=1.6, color=INK,
                spaceBefore=6, spaceAfter=6,
            ))
            story.append(Paragraph(
                "Next Practice Suggestions 下次训练建议",
                styles["h3"],
            ))
            for index, item in enumerate(practice_items, 1):
                story.append(Paragraph(
                    f'<font color="#19395C"><b>{index:02d}</b></font>　'
                    f"{_escape(item)}",
                    styles["criterionBody"],
                ))
                story.append(HRFlowable(
                    width="100%", thickness=0.45, color=BLUE_LINE,
                    spaceBefore=5, spaceAfter=5,
                ))


def _replacements(items: list[Replacement], styles, corrected: bool = False) -> list:
    rows = []
    for it in items:
        target = it.improved
        arrow = f"<b>{_escape(it.original)}</b> → {_escape(target)}"
        if it.reason:
            arrow += f"　<font size=8 color='#6d665c'>({_escape(it.reason)})</font>"
        rows.append(ListItem(Paragraph(arrow, styles["bullet"]), leftIndent=10))
    return rows


def _bullets(items: list[str], styles) -> ListFlowable:
    return ListFlowable(
        [ListItem(Paragraph(_escape(x), styles["bullet"]), leftIndent=10) for x in items],
        bulletType="bullet", start="•", leftIndent=12,
    )


def _add_paragraph_feedback(story, styles, result: GradingResult) -> None:
    if not result.paragraphFeedback:
        return
    story.append(CondPageBreak(54 * mm))
    _section_heading(
        story,
        styles,
        "05",
        "Paragraph-by-Paragraph Review 逐段精修",
        "一段原文、一段判断、一张有证据的修改对照表",
    )
    for pf in result.paragraphFeedback:
        function = pf.function.strip().title()
        card_heading = f"Paragraph {pf.paragraph}" + (
            f" · {_escape(function)}" if function else ""
        )
        strengths = pf.strengthsEn[:1]
        issues = pf.issuesEn[:2]
        improvements = pf.howToImproveEn[:1]
        story.append(CondPageBreak(48 * mm))
        story.append(HRFlowable(
            width="100%", thickness=1.4, color=INK,
            spaceBefore=5, spaceAfter=5,
        ))
        heading = Table(
            [[
                Paragraph(f"<b>{card_heading}</b>", styles["h3"]),
                Paragraph(
                    _escape(function.upper()) if function else "",
                    styles["microLabel"],
                ),
            ]],
            colWidths=[125 * mm, 49 * mm],
            hAlign="LEFT",
        )
        heading.setStyle(TableStyle([
            ("ALIGN", (1, 0), (1, 0), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        story.append(heading)
        summary = Table(
            [[
                Paragraph(
                    "<b>STRENGTH 优点</b><br/>"
                    + _escape(strengths[0] if strengths else "—"),
                    styles["tableBody"],
                ),
                Paragraph(
                    (
                        "<b>ISSUE 问题</b><br/>"
                        + "<br/>".join(
                            f"• {_escape(item)}" for item in issues
                        )
                    ) if issues else "<b>ISSUE 问题</b><br/>—",
                    styles["tableBody"],
                ),
                Paragraph(
                    "<b>SUGGESTION 建议</b><br/>"
                    + _escape(improvements[0] if improvements else "—"),
                    styles["tableBody"],
                ),
            ]],
            colWidths=[58 * mm, 58 * mm, 58 * mm],
            hAlign="LEFT",
        )
        summary.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (0, 0), GREEN),
            ("BACKGROUND", (1, 0), (1, 0), RED),
            ("BACKGROUND", (2, 0), (2, 0), YELLOW),
            ("LINEABOVE", (0, 0), (-1, 0), 0.5, BLUE_LINE),
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, BLUE_LINE),
            ("INNERGRID", (0, 0), (-1, 0), 0.4, BLUE_LINE),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]))
        story.append(summary)
        replacements = [
            *pf.vocabularyUpgrades,
            *pf.grammarCorrections,
            *pf.sentenceUpgrades,
        ]
        if replacements:
            rows = [[
                Paragraph("<b>原文证据</b>", styles["tableHead"]),
                Paragraph("<b>AI 优化表达</b>", styles["tableHead"]),
                Paragraph("<b>修改原因</b>", styles["tableHead"]),
            ]]
            for change in replacements:
                rows.append([
                    Paragraph(_escape(change.original or "—"), styles["tableEnglish"]),
                    Paragraph(_escape(change.improved or "—"), styles["tableEnglishBlue"]),
                    Paragraph(_escape(change.reason or "表达更正式、准确或自然。"), styles["tableBody"]),
                ])
            correction_table = Table(
                rows,
                colWidths=[52 * mm, 63 * mm, 59 * mm],
                repeatRows=1,
                splitByRow=1,
                hAlign="LEFT",
            )
            correction_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), BLUE_DARK),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("BACKGROUND", (1, 1), (1, -1), GREEN),
                ("BACKGROUND", (0, 1), (0, -1), BLUE),
                ("BACKGROUND", (2, 1), (2, -1), PAPER),
                ("INNERGRID", (0, 0), (-1, -1), 0.35, BLUE_LINE),
                ("BOX", (0, 0), (-1, -1), 0.6, BLUE_LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]))
            story.append(Spacer(1, 6))
            story.append(correction_table)
        if pf.sentenceUpgrade:
            original = pf.sentenceUpgrade.get("original", "")
            improved = pf.sentenceUpgrade.get("improved", "")
            if original or improved:
                comparison = Table(
                    [[
                        Paragraph(
                            _labelled_text("Original 原句", original, styles),
                            styles["tableEnglish"],
                        ),
                        Paragraph(
                            _labelled_text("Improved 改后句", improved, styles),
                            styles["tableEnglishBlue"],
                        ),
                    ]],
                    colWidths=[87 * mm, 87 * mm],
                    hAlign="LEFT",
                )
                comparison.setStyle(TableStyle([
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("BACKGROUND", (0, 0), (0, 0), PAPER),
                    ("BACKGROUND", (1, 0), (1, 0), BLUE),
                    ("BOX", (0, 0), (-1, -1), 0.5, BLUE_LINE),
                    ("INNERGRID", (0, 0), (-1, -1), 0.5, BLUE_LINE),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ]))
                story.append(Spacer(1, 5))
                story.append(comparison)


def _add_rewrite(story, styles, result: GradingResult) -> None:
    if not result.rewrittenEssay.strip():
        return
    story.append(Paragraph("Band 7.5-8.5 Rewrite 高分重写", styles["h2"]))
    for p in _para_block(result.rewrittenEssay, styles):
        story.append(p)


def _add_target_rewrites(story, styles, result: GradingResult) -> None:
    rewrites = [r for r in result.targetRewrites if r.essay.strip()]
    if not rewrites:
        return
    story.append(Paragraph("Target Band Rewrite 目标 Band 重写", styles["h2"]))
    for rewrite in rewrites:
        label = rewrite.label or "Target rewrite"
        story.append(Paragraph(f"Band {rewrite.band:g} · {_escape(label)}", styles["h3"]))
        if rewrite.focus:
            story.append(Paragraph(_escape(rewrite.focus), styles["small"]))
        for p in _para_block(rewrite.essay, styles):
            story.append(p)


def _add_sentence_comparisons(story, styles, result: GradingResult) -> None:
    if not result.sentenceComparisons:
        return
    story.append(Paragraph("Sentence-by-sentence 逐句对照修改", styles["h2"]))
    for item in result.sentenceComparisons:
        title = f"Paragraph {item.paragraph}" if item.paragraph else "Sentence"
        block: list = [Paragraph(title, styles["h3"])]
        block.append(Paragraph(f"<b>Original 原句</b><br/>{_escape(item.original)}", styles["body"]))
        block.append(Paragraph(f"<b>Improved 改后</b><br/>{_escape(item.improved)}", styles["essay"]))
        if item.reason:
            block.append(Paragraph(f"<b>Why 为什么</b><br/>{_escape(item.reason)}", styles["small"]))
        block.append(Spacer(1, 4))
        story.append(KeepTogether(block))


def _add_ideas_and_collocations(story, styles, result: GradingResult) -> None:
    if result.brainstormingIdeas or result.collocations:
        _section_heading(
            story,
            styles,
            "09",
            "What to Keep & What to Train 保留与训练",
            "把本篇的高分表达、观点和例子变成可复用的学习资产",
        )
    if result.brainstormingIdeas:
        story.append(Paragraph(
            "Brainstorming Ideas 话题素材",
            styles["sectionYellow"],
        ))
        story.append(Paragraph(
            "<br/>".join(
                f"• {_escape(item)}" for item in result.brainstormingIdeas
            ),
            styles["yellowCard"],
        ))
    if result.collocations:
        story.append(Paragraph(
            "Collocations & Patterns 高级词伙与句型",
            styles["sectionBlue"],
        ))
        items = []
        for item in result.collocations:
            text = f"<b>{_escape(item.expression)}</b>"
            if item.translation:
                text += f"<br/><font size=8 color='#6d665c'>{_escape(item.translation)}</font>"
            items.append(ListItem(Paragraph(text, styles["bullet"]), leftIndent=10))
        story.append(ListFlowable(items, bulletType="bullet", start="•", leftIndent=12))


def _add_final_takeaway(story, styles, result: GradingResult) -> None:
    if not (result.summary.strip() or result.examinerWarnings):
        return
    _section_heading(
        story,
        styles,
        "10",
        "Final Takeaway 最终带走的结论",
        "完成一次批改后，下一次写作应该具体改变什么",
    )
    if result.summary.strip():
        summary = Table(
            [["", Paragraph(_escape(result.summary), styles["commentaryText"])]],
            colWidths=[2 * mm, 172 * mm],
            hAlign="LEFT",
        )
        summary.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, 0), GREEN_DARK),
            ("BACKGROUND", (1, 0), (1, 0), GREEN),
            ("LEFTPADDING", (0, 0), (0, 0), 0),
            ("RIGHTPADDING", (0, 0), (0, 0), 0),
            ("LEFTPADDING", (1, 0), (1, 0), 9),
            ("RIGHTPADDING", (1, 0), (1, 0), 9),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(summary)
    if result.examinerWarnings:
        story.append(Spacer(1, 8))
        story.append(Paragraph("需要继续留意", styles["yellowTitle"]))
        story.append(Paragraph(
            "<br/>".join(f"• {_escape(item)}" for item in result.examinerWarnings),
            styles["yellowCard"],
        ))


def _render_value(value) -> str:
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)
