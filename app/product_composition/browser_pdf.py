"""Reference-template PDF export for the DraftLoop browser report.

The attached University Education report is the visual authority for this
export. This renderer intentionally keeps the paper report sparse: a serif
cover, numbered section rails, pale essay blocks, restrained evidence panels,
and dark table headers.
"""
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    CondPageBreak,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
    Image,
)

from app.core.submission import EssayVersion
from .browser_projection import detailed_projection
from .report_typography import register_report_fonts, report_markup


CRITERIA = {
    "TR": "Task Response",
    "TA": "Task Achievement",
    "CC": "Coherence & Cohesion",
    "LR": "Lexical Resource",
    "GRA": "Grammatical Range & Accuracy",
}

NAVY = colors.HexColor("#17233C")
NAVY_2 = colors.HexColor("#23365D")
MUTED = colors.HexColor("#6C7891")
GOLD = colors.HexColor("#B98036")
TEAL = colors.HexColor("#3D7D6C")
PAPER = colors.HexColor("#FFFFFF")
ESSAY_FILL = colors.HexColor("#F3F5F7")
GREEN_FILL = colors.HexColor("#F1F8F5")
CREAM_FILL = colors.HexColor("#FFF8EC")
RULE = colors.HexColor("#D4DCE5")
GRID = colors.HexColor("#D9E0E8")


def render_report_pdf(report, source, *, image_bytes=None):
    """Render a verified browser report using the supplied DOCX's layout."""
    register_report_fonts()
    essay = EssayVersion.create(source["candidateScript"])
    original = [essay.original_text[p.locator.start:p.locator.end] for p in essay.paragraphs]
    detail = report.get("detailedReport")
    if detail is not None:
        detail = detailed_projection(detail, report["lockedScoreSha256"])
        if (detail["essayVersionId"] != essay.essay_version_id
                or [p["original"] for p in detail["paragraphs"]] != original):
            raise ValueError("PDF_SOURCE_VERSION_MISMATCH")

    width = LETTER[0] - 36 * mm
    body = ParagraphStyle(
        "reference-body", fontName="ReportSongti", fontSize=10.2, leading=15.6,
        textColor=NAVY, wordWrap="CJK", spaceAfter=5, allowWidows=0,
        allowOrphans=0,
    )
    english = ParagraphStyle(
        "reference-english", parent=body, fontName="ReportTimes", fontSize=10.4,
        leading=15.6,
    )
    essay_style = ParagraphStyle(
        "reference-essay", parent=english, fontSize=11.1, leading=17.2,
        spaceAfter=0,
    )
    body_bold = ParagraphStyle("reference-body-bold", parent=body, fontName="ReportSongtiBold")
    small = ParagraphStyle(
        "reference-small", parent=body, fontSize=8.8, leading=12.8,
        textColor=MUTED, spaceAfter=3,
    )
    kicker = ParagraphStyle(
        "reference-kicker", parent=english, fontName="ReportTimesBold", fontSize=11,
        leading=13, textColor=GOLD, spaceAfter=13,
    )
    cover_title = ParagraphStyle(
        "reference-cover-title", parent=english, fontName="ReportTimesBold", fontSize=31,
        leading=35, textColor=NAVY, spaceAfter=3,
    )
    cover_subtitle = ParagraphStyle(
        "reference-cover-subtitle", parent=english, fontName="ReportTimesBold", fontSize=24,
        leading=29, textColor=NAVY, spaceAfter=20,
    )
    cover_meta = ParagraphStyle(
        "reference-cover-meta", parent=english, fontName="ReportTimesBold", fontSize=12,
        leading=15, textColor=MUTED, spaceAfter=26,
    )
    marker_index = ParagraphStyle(
        "reference-marker-index", parent=english, fontName="ReportTimesBold", fontSize=10,
        leading=12, textColor=GOLD, spaceAfter=0,
    )
    marker_squares = ParagraphStyle(
        "reference-marker-squares", parent=body, fontSize=11, leading=12,
        textColor=MUTED, spaceAfter=0,
    )
    marker_label = ParagraphStyle(
        "reference-marker-label", parent=english, fontName="ReportTimes", fontSize=10,
        leading=12, textColor=MUTED, spaceAfter=0,
    )
    section_heading = ParagraphStyle(
        "reference-section-heading", parent=english, fontName="ReportTimesBold", fontSize=17,
        leading=21, textColor=NAVY, spaceAfter=7,
    )
    paragraph_heading = ParagraphStyle(
        "reference-paragraph-heading", parent=english, fontName="ReportTimesBold", fontSize=16,
        leading=20, textColor=NAVY, spaceAfter=7,
    )
    score_label = ParagraphStyle(
        "reference-score-label", parent=english, fontName="ReportTimesBold", fontSize=9.2,
        leading=11, textColor=MUTED, alignment=1, spaceAfter=3,
    )
    score_value = ParagraphStyle(
        "reference-score-value", parent=english, fontName="ReportTimesBold", fontSize=24,
        leading=27, textColor=NAVY, alignment=1, spaceAfter=0,
    )
    score_value_overall = ParagraphStyle(
        "reference-score-value-overall", parent=score_value, fontSize=25, textColor=GOLD,
    )
    score_value_gra = ParagraphStyle(
        "reference-score-value-gra", parent=score_value, textColor=TEAL,
    )
    table_head = ParagraphStyle(
        "reference-table-head", parent=english, fontName="ReportTimesBold", fontSize=8.6,
        leading=10.8, textColor=colors.white, spaceAfter=0,
    )
    table_body = ParagraphStyle(
        "reference-table-body", parent=english, fontSize=9.1, leading=12.7,
        textColor=NAVY, spaceAfter=0,
    )
    map_style = ParagraphStyle(
        "reference-map", parent=english, fontSize=11, leading=17, textColor=NAVY,
        spaceAfter=0,
    )
    bullet_style = ParagraphStyle(
        "reference-bullet", parent=english, fontSize=10.8, leading=16.5,
        leftIndent=12, firstLineIndent=-10, textColor=NAVY, spaceAfter=2,
    )

    def p(text, style=body, bold=False):
        return Paragraph(report_markup(text, bold=bold), style)

    def section_marker(index, label, *, squares="□□□□□□"):
        cells = [p(index, marker_index), p(squares, marker_squares), p(label, marker_label), ""]
        item = Table([cells], colWidths=[13 * mm, 47 * mm, 105 * mm, width - 165 * mm], hAlign="LEFT")
        item.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LINEBELOW", (0, 0), (-1, 0), .8, RULE),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]))
        return item

    def accent_box(content, fill=ESSAY_FILL, edge=GOLD, padding=8):
        item = Table([["", content]], colWidths=[2.2 * mm, width - 2.2 * mm], hAlign="LEFT")
        item.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, 0), edge), ("BACKGROUND", (1, 0), (1, 0), fill),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (0, 0), 0), ("RIGHTPADDING", (0, 0), (0, 0), 0),
            ("LEFTPADDING", (1, 0), (1, 0), padding), ("RIGHTPADDING", (1, 0), (1, 0), padding),
            ("TOPPADDING", (1, 0), (1, 0), padding), ("BOTTOMPADDING", (1, 0), (1, 0), padding),
            ("BOX", (1, 0), (1, 0), .55, GRID),
        ]))
        return item

    def score_grid(values, *, compact=False):
        labels = [("OVERALL", values[0][1], score_value_overall)] + [
            (key, value, score_value_gra if key == "GRA" else score_value)
            for key, value in values[1:]
        ]
        cells = []
        for label_text, value, value_style in labels:
            cells.append([p(label_text, score_label), p(format(value, ".1f"), value_style, True)])
        item = Table([cells], colWidths=[width / 5] * 5, hAlign="LEFT")
        item.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), .75, GRID), ("INNERGRID", (0, 0), (-1, -1), .55, GRID),
            ("BACKGROUND", (0, 0), (0, 0), CREAM_FILL),
            ("BACKGROUND", (1, 0), (-1, 0), PAPER),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 7 if compact else 9),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7 if compact else 9),
            ("TOPPADDING", (0, 0), (-1, -1), 9 if compact else 12),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8 if compact else 11),
        ]))
        return item

    def matrix(rows, widths):
        item = Table(rows, colWidths=widths, repeatRows=1, splitByRow=1, splitInRow=1, hAlign="LEFT")
        commands = [
            ("BACKGROUND", (0, 0), (-1, 0), NAVY_2),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), .45, GRID),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]
        for row in range(1, len(rows)):
            commands.append(("BACKGROUND", (0, row), (-1, row), PAPER if row % 2 else ESSAY_FILL))
        item.setStyle(TableStyle(commands))
        return item

    criteria_keys = ["TA" if "TA" in report["criteria"] else "TR", "CC", "LR", "GRA"]
    score_values = [("OVERALL", report["overallBand"])] + [(key, report["criteria"][key]) for key in criteria_keys]
    task_label = "Academic Writing Task 1" if "TA" in report["criteria"] else "Academic Writing Task 2"
    topic = (detail or {}).get("topicLearning", {}).get("theme", "") if detail else ""
    topic = topic or "University Education"
    strongest = " / ".join(CRITERIA.get(c, c) for c in (detail or {}).get("strongestCriteria", []))

    story = [
        Spacer(1, 22 * mm),
        p("DRAFTLOOP", kicker, True),
        p("IELTS Writing", cover_title, True),
        p("· Final Detailed Report", cover_subtitle, True),
        p(task_label + " · " + topic, cover_meta, True),
        score_grid(score_values),
        Spacer(1, 6),
        p(" · ".join([format(report["overallBand"], ".1f")] + [f"{k} {report['criteria'][k]:.1f}" for k in criteria_keys]),
          ParagraphStyle("reference-score-line", parent=english, fontName="ReportTimesBold", fontSize=11,
                         leading=14, alignment=1, textColor=NAVY, spaceAfter=12), True),
    ]
    cover_note = "强项：" + strongest if strongest else "本稿已完成评分与逐段证据整理。"
    story += [accent_box(p(cover_note, body), CREAM_FILL, GOLD, padding=10), PageBreak()]

    story.append(section_marker("00", "Original Essay"))
    story.append(Spacer(1, 7))
    for index, chunk in enumerate(original, 1):
        story.append(p("Paragraph " + str(index), section_heading, True))
        story.append(accent_box(p(chunk, essay_style), ESSAY_FILL, GOLD, padding=9))
        story.append(Spacer(1, 13))
    story.append(section_marker("01", "Score Overview"))
    story.append(p("评分解读", small, True))
    story.append(score_grid(score_values, compact=True))
    if detail:
        story.append(Spacer(1, 8))
        story.append(accent_box(p("本次重点：" + (strongest or "继续稳定四项评分表现。"), body), CREAM_FILL, GOLD, padding=9))
        story.append(CondPageBreak(75 * mm))
        story.append(section_marker("02", "TR · CC · LR · GRA"))
        for criterion in detail["criterionAnalyses"]:
            key = criterion["criterion"]
            title_text = CRITERIA[key] + " (" + key + ") — " + format(report["criteria"][key], ".1f")
            story.append(p(title_text, section_heading, True))
            story.append(p(criterion["analysis"]))
            if criterion["strengths"]:
                story.append(p("做得好的地方　" + "；".join(criterion["strengths"]), small))
            if criterion["limitations"]:
                story.append(p("限制这一项的因素　" + "；".join(criterion["limitations"]), small))
            story.append(Spacer(1, 8))

        story.append(section_marker("03", "Strength & Main Bottleneck"))
        story.append(Spacer(1, 7))
        story.append(accent_box(p("KEEP　" + (strongest or "当前评分中表现稳定的部分。"), body_bold), GREEN_FILL, TEAL, padding=10))
        story.append(Spacer(1, 10))
        bottleneck = "；".join(detail["criterionAnalyses"][0]["limitations"]) if detail["criterionAnalyses"] else "下一步优先处理最具体、最影响目标分的一个问题。"
        audit = report.get("calibrationAudit")
        if audit:
            audit_label = {"RESCORED": "RAG 审核触发了 Rubric 重新评分", "ACCEPTED_INITIAL": "RAG 独立审核完成，未触发重新评分", "NOT_AVAILABLE": "本次未进行 RAG 审核"}.get(audit["status"], "评分审核状态待核验")
            bottleneck += "\n评分审核：" + audit_label
        story.append(accent_box(p(bottleneck, body), CREAM_FILL, GOLD, padding=10))

        story.append(Spacer(1, 12))
        story.append(section_marker("04", "Essay Mind Map"))
        story.append(p("考生文章思路", small, True))
        map_lines = ["中心立场\n" + detail["mindMap"]["thesis"]]
        branch_prefix = ["Introduction", "Body 1", "Body 2", "Conclusion"]
        for i, branch in enumerate(detail["mindMap"]["branches"]):
            name = branch_prefix[i] if i < len(branch_prefix) else "Paragraph " + str(branch["paragraphIndex"])
            lines = [name + "  " + branch["role"], "├─ " + branch["point"]]
            for support in branch["support"]:
                lines.append("├─ " + support)
            if branch["gap"]:
                lines.append("└─ 待补全：" + branch["gap"])
            map_lines.append("\n".join(lines))
        story.append(accent_box(p("\n\n".join(map_lines), map_style), ESSAY_FILL, NAVY_2, padding=11))

        story.append(Spacer(1, 12))
        story.append(section_marker("05", "Paragraph-by-Paragraph Review"))
        story.append(p("逐段精修　·　根据修改建议重写", small, True))
        impact_labels = {"SUPPORTS": "支撑整体表现", "LIMITS": "限制整体表现", "NEUTRAL": "表现稳定"}
        for para in detail["paragraphs"]:
            story.append(CondPageBreak(55 * mm))
            story.append(p("Paragraph " + str(para["index"]) + "   " + para["role"], paragraph_heading, True))
            correction_labels = " / ".join(sorted({c["kind"] for c in para["corrections"]})) or "/"
            estimate = para["targetBandEstimate"] if para["targetBandEstimate"] is not None else report["overallBand"]
            score_row = [[
                p("≈ " + format(estimate, ".1f"), table_body, True),
                p(correction_labels, table_body),
                p(impact_labels[para["impact"]], table_body, True),
            ]]
            score_item = Table(score_row, colWidths=[width / 3] * 3, hAlign="LEFT")
            score_item.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), ESSAY_FILL), ("GRID", (0, 0), (-1, -1), .45, GRID),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]))
            story.append(score_item)
            story.append(Spacer(1, 7))
            story.append(p(para["assessment"], body))
            story.append(accent_box(p(para["original"], essay_style), ESSAY_FILL, GOLD, padding=9))
            if para["corrections"]:
                rows = [[p("原文", table_head, True), p("AI", table_head, True), p("修改原因", table_head, True)]]
                for change in para["corrections"]:
                    rows.append([
                        p("此段缺少相应内容" if change["location"]["missing"] else change["location"]["quote"], table_body),
                        p(change["replacement"] or "删去重复表达", table_body),
                        p(change["reason"], table_body),
                    ])
                story.append(Spacer(1, 7))
                story.append(matrix(rows, [width * .30, width * .38, width * .32]))
                story.append(Spacer(1, 7))
                story.append(accent_box(p("KEEP　" + ("；".join(para["changes"]) or "保留原有清晰表达。"), body), GREEN_FILL, TEAL, padding=9))
                story.append(Spacer(1, 8))

        story.append(CondPageBreak(65 * mm))
        story.append(section_marker("06", "Only Meaningful Changes"))
        changes = []
        for para in detail["paragraphs"]:
            for change in para["corrections"]:
                changes.append([
                    p("Body " + str(para["index"]), table_body),
                    p("此段缺少相应内容" if change["location"]["missing"] else change["location"]["quote"], table_body),
                    p(change["replacement"] or "KEEP", table_body),
                    p(change["reason"], table_body),
                    p(change["kind"], table_body),
                ])
        if not changes:
            changes.append([p("-", table_body), p("本次没有需要单独列出的修改。", table_body), p("KEEP", table_body), p("", table_body), p("", table_body)])
        story.append(matrix([[p("段落", table_head, True), p("原文", table_head, True), p("AI", table_head, True), p("原因", table_head, True), p("类型", table_head, True)]] + changes,
                            [width * .14, width * .25, width * .29, width * .22, width * .10]))

        story.append(Spacer(1, 12))
        story.append(section_marker("07", "Minimal Full-Essay Version"))
        story.append(p("优化后的完整文章", small, True))
        for para in detail["paragraphs"]:
            story.append(p("Paragraph " + str(para["index"]), section_heading, True))
            story.append(accent_box(p(para["optimized"], essay_style), ESSAY_FILL, GOLD, padding=9))
            story.append(Spacer(1, 10))

        story.append(CondPageBreak(70 * mm))
        topic_data = detail["topicLearning"]
        story.append(section_marker("08", "Topic Knowledge Pack"))
        story.append(p("主题积累　·　" + topic_data["theme"], small, True))
        story.append(accent_box(p(topic_data["theme"], map_style), ESSAY_FILL, NAVY_2, padding=10))
        if topic_data["expressions"]:
            story.append(Spacer(1, 13))
            story.append(p("A.", section_heading, True))
            for item in topic_data["expressions"]:
                story.append(p("• " + item["expression"], bullet_style, True))
                story.append(p(item["meaning"] + "　" + item["usage"], small))
        for example in topic_data["examples"]:
            story.append(Spacer(1, 7))
            story.append(p("B.　" + example["title"], section_heading, True))
            story.append(p("• " + example["scenario"], bullet_style))
            story.append(p("• 论证连接　" + example["structure"], bullet_style))
            story.append(p("• 迁移方法　" + example["adaptation"], bullet_style))
        if not topic_data["expressions"] and not topic_data["examples"]:
            story.append(p("• 本篇没有需要单独积累的新素材，先把逐段修改练熟。", bullet_style))

        story.append(Spacer(1, 12))
        story.append(section_marker("09", "For Future ML / Personalization"))
        ml_rows = [[p("状态", table_head, True), p("保留内容", table_head, True), p("调整与迁移", table_head, True)]]
        stable = [e["expression"] for e in topic_data["expressions"] if e["source"] == "CANDIDATE"]
        optimized = [e["expression"] for e in topic_data["expressions"] if e["source"] == "OPTIMIZED"]
        ml_rows += [
            [p("STABLE", table_body), p("；".join(stable) or "当前文章中的有效表达", table_body), p("KEEP", table_body)],
            [p("OPTIMIZE WHEN NEEDED", table_body), p("；".join(optimized) or "只在具体证据支持时修改", table_body), p("小幅调整", table_body)],
            [p("EXPOSURE", table_body), p("独立判断、比较不同观点", table_body), p("积累后迁移到相似话题", table_body)],
            [p("REUSABLE EXAMPLE", table_body), p("；".join(e["title"] for e in topic_data["examples"]) or "待积累", table_body), p("适配新题目", table_body)],
            [p("ARGUMENT MODULE", table_body), p("观点 → 机制 → 结果", table_body), p("下一次写作复用", table_body)],
        ]
        story.append(matrix(ml_rows, [width * .25, width * .45, width * .30]))

        story.append(Spacer(1, 12))
        story.append(section_marker("10", "What to Keep & What to Train"))
        story.append(p("下一步训练", small, True))
        for action in detail["nextActions"]:
            story.append(p("• " + action["title"], bullet_style, True))
            for step in action["steps"]:
                story.append(p("• " + step, bullet_style))
            story.append(p("完成标准　" + action["successCheck"], small))
        training_rows = [[p("序号", table_head, True), p("训练动作", table_head, True), p("完成标准", table_head, True)]]
        for i, action in enumerate(detail["nextActions"], 1):
            training_rows.append([p(str(i) + ".", table_body), p(action["title"], table_body), p(action["successCheck"], table_body)])
        story.append(matrix(training_rows, [width * .12, width * .48, width * .40]))

        story.append(Spacer(1, 12))
        story.append(section_marker("11", "Final Takeaway"))
        story.append(accent_box(p(detail["encouragement"], body), GREEN_FILL, TEAL, padding=10))
        story.append(Spacer(1, 10))
        story.append(accent_box(p("下一步先处理最影响目标分的具体问题，再保持已经完成得好的表达。", body), CREAM_FILL, GOLD, padding=10))
        story.append(Spacer(1, 10))
        story.append(accent_box(p("最终分数由 Rubric 判断锁定；RAG 只承担独立审核与校准证据的角色。", body), ESSAY_FILL, NAVY_2, padding=10))
    else:
        story.append(CondPageBreak(70 * mm))
        story.append(section_marker("02", "Score Overview"))
        story.append(p("评分解读", small, True))
        for key in criteria_keys:
            story.append(p(CRITERIA[key] + " (" + key + ") — " + format(report["criteria"][key], ".1f"), section_heading, True))
        story.append(section_marker("11", "Final Takeaway"))
        story.append(accent_box(p("本稿原文已保留，重新批改后可生成逐段修改与主题积累。", body), CREAM_FILL, GOLD, padding=10))
        for report_section in report.get("sections", []):
            story.append(p(report_section["label"], section_heading, True))
            for item in report_section["records"]:
                story.append(p(item["text"]))
                if item.get("textEn") and item["textEn"] != item["text"]:
                    story.append(p(item["textEn"], english))
        story.append(section_marker("附录", "题目与本稿原文"))
        story += [p(block, essay_style) for block in original]

    if image_bytes is not None:
        story.append(Spacer(1, 10))
        w, h = ImageReader(BytesIO(image_bytes)).getSize()
        scale = min(width / w, 300 / h, 1)
        story.append(Image(BytesIO(image_bytes), width=w * scale, height=h * scale, hAlign="LEFT"))

    def page_chrome(canvas, document):
        canvas.saveState()
        canvas.setFillColor(NAVY)
        canvas.setFont("ReportTimesBold", 9)
        canvas.drawString(18 * mm, LETTER[1] - 11 * mm, "DraftLoop")
        canvas.setFillColor(MUTED)
        canvas.setFont("ReportTimes", 9)
        canvas.drawString(34 * mm, LETTER[1] - 11 * mm, "· AI Writing Coach for IELTS")
        canvas.setFont("ReportTimes", 9)
        canvas.drawRightString(LETTER[0] - 18 * mm, 10 * mm, "Page " + str(document.page))
        canvas.restoreState()

    stream = BytesIO()
    doc = SimpleDocTemplate(
        stream, pagesize=LETTER, rightMargin=18 * mm, leftMargin=18 * mm,
        topMargin=18 * mm, bottomMargin=16 * mm,
        title="DraftLoop · Detailed IELTS Writing Report", author="DraftLoop",
    )
    doc.build(story, onFirstPage=page_chrome, onLaterPages=page_chrome)
    return stream.getvalue()
