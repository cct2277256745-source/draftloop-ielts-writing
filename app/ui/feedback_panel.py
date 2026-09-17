"""右栏：反馈与范文栏 —— 打分卡 / 逐段建议 / 高分重写 / 素材词伙 + 导出PDF。

set_result(...) 后动态重建滚动区内容；空状态显示引导提示。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from ..core.models import (
    Collocation, GradingResult, MemoriseWorthyExpression, ParagraphFeedback,
    Replacement, ScoreDiagnosis, SyntaxUpgrade, ValidatorDecision, VocabularyUpgrade,
)
from ..core.workflow import WorkflowResult, WorkflowStatus

class FeedbackPanel(QFrame):
    export_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("pane")
        self.setProperty("class", "pane")
        self._result: GradingResult | None = None
        self._workflow_result: WorkflowResult[GradingResult] | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        eyebrow = QLabel("EXAMINER REPORT")
        eyebrow.setProperty("class", "eyebrow")
        title = QLabel("反馈与范文栏")
        title.setProperty("class", "paneTitle")
        title_box.addWidget(eyebrow)
        title_box.addWidget(title)
        header.addLayout(title_box)
        header.addStretch()
        self.clear_btn = QPushButton("清除")
        self.clear_btn.setProperty("class", "clearButton")
        self.clear_btn.clicked.connect(self.clear)
        header.addWidget(self.clear_btn)

        self.export_btn = QPushButton("导出 PDF")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self.export_requested.emit)
        header.addWidget(self.export_btn)
        root.addLayout(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        root.addWidget(self.scroll, 1)

        self._show_empty()

    # ---------- 内容容器 ----------
    def _fresh_container(self) -> QVBoxLayout:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(2, 2, 8, 2)
        layout.setSpacing(12)
        layout.setAlignment(Qt.AlignTop)
        self.scroll.setWidget(container)
        return layout

    def _show_empty(self) -> None:
        self._result = None
        self._workflow_result = None
        self.export_btn.setEnabled(False)
        layout = self._fresh_container()
        box = QFrame()
        box.setObjectName("emptyState")
        inner = QVBoxLayout(box)
        inner.setContentsMargins(24, 40, 24, 40)
        inner.setSpacing(8)
        inner.setAlignment(Qt.AlignCenter)
        icon = QLabel("✺")
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet("font-size: 26px; color:#c9c3b8;")
        t = QLabel("等待考官反馈")
        t.setObjectName("emptyTitle")
        t.setAlignment(Qt.AlignCenter)
        h = QLabel("输入题目与作文后点击「开始批改」，\n即可生成评分、逐段建议、高分重写与话题语料。")
        h.setObjectName("emptyHint")
        h.setAlignment(Qt.AlignCenter)
        inner.addWidget(icon)
        inner.addWidget(t)
        inner.addWidget(h)
        layout.addWidget(box)
        layout.addStretch()

    # ---------- 状态 ----------
    def set_busy(self, busy: bool) -> None:
        if busy:
            self._result = None
            self._workflow_result = None
            self.export_btn.setEnabled(False)
            layout = self._fresh_container()
            label = QLabel("正在以雅思考官视角批改，请稍候……")
            label.setObjectName("corpusStatus")
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet("padding: 40px; font-size: 13px; color:#6d665c;")
            layout.addWidget(label)
            layout.addStretch()

    @property
    def result(self) -> GradingResult | None:
        return self._result

    @property
    def workflow_result(self) -> WorkflowResult[GradingResult] | None:
        return self._workflow_result

    def set_workflow_result(
        self,
        outcome: WorkflowResult[GradingResult],
        question: str = "",
        essay: str = "",
    ) -> None:
        self._workflow_result = outcome
        if (
            outcome.status is WorkflowStatus.COMPLETE
            and outcome.completeness
            and outcome.payload is not None
        ):
            self.set_result(outcome.payload, question, essay)
            return

        self._result = None
        self.export_btn.setEnabled(False)
        layout = self._fresh_container()
        box = QFrame()
        box.setObjectName("workflowStatus")
        inner = QVBoxLayout(box)
        inner.setContentsMargins(24, 32, 24, 32)
        inner.setSpacing(8)

        title = QLabel(f"WORKFLOW {outcome.status.value}")
        title.setObjectName("workflowStatusTitle")
        title.setAlignment(Qt.AlignCenter)
        title.setWordWrap(True)
        inner.addWidget(title)

        status_text = {
            WorkflowStatus.PARTIAL: "批改流程未完整完成，不能生成或导出正式报告。",
            WorkflowStatus.REVIEW_REQUIRED: "当前结果需要人工复核，不能生成或导出正式报告。",
            WorkflowStatus.FAILED: "批改流程失败，未生成报告。",
        }.get(outcome.status, "当前流程未生成完整报告。")
        summary = QLabel(status_text)
        summary.setObjectName("workflowStatusSummary")
        summary.setAlignment(Qt.AlignCenter)
        summary.setWordWrap(True)
        inner.addWidget(summary)

        safe_states = [
            f"{failure.stage.value}: {failure.state.value}"
            for failure in outcome.stage_failures
        ]
        safe_states.extend(reason.value for reason in outcome.review_reasons)
        if safe_states:
            details = QLabel("\n".join(safe_states))
            details.setObjectName("workflowStatusDetails")
            details.setAlignment(Qt.AlignCenter)
            details.setWordWrap(True)
            inner.addWidget(details)
        layout.addWidget(box)
        layout.addStretch()

    def set_result(
        self,
        result: GradingResult,
        question: str = "",
        essay: str = "",
    ) -> None:
        self._result = result
        self._workflow_result = WorkflowResult.complete(result)
        self.export_btn.setEnabled(True)
        layout = self._fresh_container()

        layout.addWidget(self._build_scores(result))

        if result.scoreDiagnosis.currentLevelSummaryEn or result.scoreDiagnosis.currentLevelSummaryZh:
            layout.addWidget(self._build_score_diagnosis(result.scoreDiagnosis))

        if result.examinerWarnings:
            for w in result.examinerWarnings:
                warn = QLabel("⚠ " + w)
                warn.setProperty("class", "warnBox")
                warn.setWordWrap(True)
                layout.addWidget(warn)

        if result.taskType == "task1":
            if result.chartUnderstanding:
                layout.addWidget(self._dict_section(
                    "Chart Understanding 图表理解", "主批改路由基于原图提取的关键信息",
                    result.chartUnderstanding,
                ))
            if result.dataAccuracyCheck:
                layout.addWidget(self._dict_section(
                    "Data Accuracy Check 数据准确性", "最终审稿已再次对照原图",
                    result.dataAccuracyCheck,
                ))
            if result.overviewCheck:
                layout.addWidget(self._dict_section(
                    "Overview Check 概述检查", "检查是否概括主要趋势与对比",
                    result.overviewCheck,
                ))

        if question.strip() or essay.strip() or result.paragraphFeedback:
            review_parts: list[QWidget] = []
            if question.strip():
                review_parts.append(self._editorial_block(
                    "QUESTION 题目",
                    self._english_label(question),
                    "questionBlock",
                ))
            if essay.strip():
                review_parts.append(self._editorial_block(
                    "CANDIDATE ESSAY 考生原文",
                    self._candidate_essay(essay),
                    "candidateBlock",
                ))
            if result.paragraphFeedback:
                review_parts.extend(
                    self._build_paragraph(pf) for pf in result.paragraphFeedback
                )
            layout.addWidget(self._section_group(
                "02 · Candidate Essay & Paragraph Feedback",
                "考生原文与逐段修改建议",
                review_parts,
                "paragraphFeedbackSection",
            ))

        if result.vocabularyUpgrades or result.syntaxUpgrades:
            language_parts: list[QWidget] = []
            if result.vocabularyUpgrades:
                language_parts.append(self._subsection_heading(
                    "Vocabulary Upgrade 词汇升级"
                ))
                language_parts.append(
                    self._build_vocabulary_table(result.vocabularyUpgrades)
                )
            if result.syntaxUpgrades:
                language_parts.append(self._subsection_heading(
                    "Syntax Upgrade 可迁移句法"
                ))
                language_parts.append(
                    self._syntax_grid(result.syntaxUpgrades)
                )
            layout.addWidget(self._section_group(
                "03 · Language Upgrade 语言升级",
                "词汇升级与可迁移句法",
                language_parts,
                "languageUpgradeSection",
            ))

        if result.balancedFinalVersion.strip():
            layout.addWidget(self._section_card(
                "04 · Final Revised Essay 最终复核版范文",
                "Recommended 推荐学习版 · 已融合自然准确表达与句法增强，并经过最终复核",
                self._panel_text(result.balancedFinalVersion),
                "finalVersionSection",
            ))

        if (
            result.memoriseWorthyExpressions
            or result.nextPracticeSuggestions
        ):
            final_parts: list[QWidget] = []
            if result.memoriseWorthyExpressions:
                final_parts.append(self._subsection_heading(
                    "Memorise-worthy Expressions 值得背表达"
                ))
                memory_grid = QWidget()
                memory_layout = QGridLayout(memory_grid)
                memory_layout.setContentsMargins(0, 0, 0, 0)
                memory_layout.setHorizontalSpacing(10)
                memory_layout.setVerticalSpacing(10)
                for index, item in enumerate(
                    result.memoriseWorthyExpressions[:8]
                ):
                    memory_layout.addWidget(
                        self._build_memorise_expression(item),
                        index // 2,
                        index % 2,
                    )
                final_parts.append(memory_grid)
            if result.nextPracticeSuggestions:
                final_parts.append(self._subsection_heading(
                    "Next Practice Suggestions 下次训练建议"
                ))
                final_parts.append(
                    self._practice_list(result.nextPracticeSuggestions[:3])
                )
            layout.addWidget(self._section_group(
                "05 · Learning & Practice 学习与训练",
                "最终复核后保留的可学习表达与下一步练习",
                final_parts,
                "finalReviewSection",
            ))

        if result.rawModelResponses:
            layout.addWidget(self._section_card(
                "Model Raw Responses 模型原始回复",
                "仅在 JSON 连续解析失败时保留，便于排查",
                self._dict_text_label(result.rawModelResponses),
            ))

        if result.brainstormingIdeas:
            layout.addWidget(self._section_card(
                "话题素材 Brainstorming Ideas",
                "可直接迁移到同话题作文的观点储备",
                self._bullet_label(result.brainstormingIdeas),
            ))

        if result.collocations:
            layout.addWidget(self._section_card(
                "高级词伙与句型 Collocations",
                "优先记忆可复用表达",
                self._collocation_label(result.collocations),
            ))

        layout.addStretch()

    def _editorial_block(
        self,
        title: str,
        content: QWidget,
        object_name: str,
    ) -> QFrame:
        block = QFrame()
        block.setProperty("class", "editorialBlock")
        block.setObjectName(object_name)
        layout = QVBoxLayout(block)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(7)
        heading = QLabel(title)
        heading.setProperty("class", "editorialLabel")
        layout.addWidget(heading)
        layout.addWidget(content)
        return block

    def _subsection_heading(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setProperty("class", "editorialSubheading")
        return label

    def _syntax_grid(self, items: list[SyntaxUpgrade]) -> QWidget:
        wrap = QWidget()
        layout = QGridLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(10)
        for index, item in enumerate(items):
            layout.addWidget(
                self._build_syntax_upgrade(item),
                index // 2,
                index % 2,
            )
        return wrap

    def _build_vocabulary_table(
        self,
        items: list[VocabularyUpgrade],
    ) -> QFrame:
        table = QFrame()
        table.setProperty("class", "vocabularyTable")
        grid = QGridLayout(table)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(0)
        grid.setVerticalSpacing(0)
        headers = (
            "Original 原表达",
            "Suggested 建议表达",
            "Why 为什么更好",
            "Recommendation",
        )
        for column, text in enumerate(headers):
            label = QLabel(text)
            label.setWordWrap(True)
            label.setProperty("class", "vocabularyHead")
            grid.addWidget(label, 0, column)
        for row, item in enumerate(items, 1):
            values = (
                item.originalExpression or "—",
                item.suggestedExpression or "—",
                item.whyBetterEn or item.whyBetterZh or "—",
                item.recommendation or "—",
            )
            for column, text in enumerate(values):
                label = QLabel(_esc(text))
                label.setWordWrap(True)
                label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                label.setProperty(
                    "class",
                    "vocabularyCell recommendationCell"
                    if column == 3 else "vocabularyCell",
                )
                grid.addWidget(label, row, column)
        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(1, 2)
        grid.setColumnStretch(2, 4)
        grid.setColumnStretch(3, 2)
        return table

    def _build_vocabulary_upgrade(self, item: VocabularyUpgrade) -> QFrame:
        block = QFrame()
        block.setProperty("class", "vocabularyCard")
        layout = QVBoxLayout(block)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(7)
        layout.addWidget(self._compare_label(
            "Original 原表达",
            item.originalExpression,
            "compareOriginal",
        ))
        layout.addWidget(self._compare_label(
            "Suggested 建议表达",
            item.suggestedExpression,
            "compareImproved",
        ))
        why = [item.whyBetterEn or item.whyBetterZh]
        why = [text for text in why if text]
        if why:
            layout.addWidget(self._mini("Why 为什么更好", why))
        if item.recommendation:
            recommendation_class = _recommendation_class(item.recommendation)
            layout.addWidget(self._tag_label(
                item.recommendation,
                recommendation_class,
            ))
        return block

    def _dict_text_label(self, values: dict[str, str]) -> QLabel:
        text = "\n\n".join(f"{key}:\n{value}" for key, value in values.items())
        return self._panel_text(text)

    def _dict_section(
        self,
        title: str,
        subtitle: str,
        data: dict,
        object_name: str = "",
    ) -> QFrame:
        items: list[str] = []
        for key, value in data.items():
            if isinstance(value, list):
                rendered = "; ".join(str(x) for x in value) or "—"
            elif isinstance(value, bool):
                rendered = "Yes" if value else "No"
            else:
                rendered = str(value)
            items.append(f"{key}: {rendered}")
        return self._section_card(
            title,
            subtitle,
            self._bullet_label(items),
            object_name,
        )

    # ---------- 构件 ----------
    def _section_title(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("class", "sectionTitle")
        return label

    def _section_card(
        self,
        title: str,
        subtitle: str,
        content: QWidget,
        object_name: str = "",
    ) -> QFrame:
        card = QFrame()
        card.setProperty("class", "reportSection")
        if object_name:
            card.setObjectName(object_name)
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 12, 14, 14)
        v.setSpacing(8)

        title_label = QLabel(title)
        title_label.setProperty("class", "reportSectionTitle")
        title_label.setWordWrap(True)
        subtitle_label = QLabel(subtitle)
        subtitle_label.setProperty("class", "reportSectionSubtitle")
        subtitle_label.setWordWrap(True)
        v.addWidget(title_label)
        v.addWidget(subtitle_label)
        v.addWidget(content)
        return card

    def _section_group(
        self,
        title: str,
        subtitle: str,
        widgets: list[QWidget],
        object_name: str,
    ) -> QFrame:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(10)
        for widget in widgets:
            layout.addWidget(widget)
        return self._section_card(title, subtitle, content, object_name)

    def _body_label(self, text: str) -> QLabel:
        label = QLabel(_paragraph_html(text))
        label.setWordWrap(True)
        label.setTextFormat(Qt.RichText)
        label.setProperty("class", "reportBody")
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        return label

    def _english_label(self, text: str) -> QLabel:
        label = QLabel(_paragraph_html(text))
        label.setWordWrap(True)
        label.setTextFormat(Qt.RichText)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setProperty("class", "englishBody")
        return label

    def _candidate_essay(self, text: str) -> QWidget:
        wrap = QWidget()
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        paragraphs = [
            part.strip()
            for part in text.replace("\r\n", "\n").split("\n\n")
            if part.strip()
        ]
        if len(paragraphs) <= 1:
            paragraphs = [part.strip() for part in text.splitlines() if part.strip()]
        for index, paragraph in enumerate(paragraphs, 1):
            label = QLabel(
                f"<b>Paragraph {index}</b><br/>{_esc(paragraph)}"
            )
            label.setWordWrap(True)
            label.setTextFormat(Qt.RichText)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            label.setProperty("class", "candidateParagraph")
            layout.addWidget(label)
        return wrap

    def _panel_text(self, text: str) -> QLabel:
        label = QLabel(_paragraph_html(text))
        label.setWordWrap(True)
        label.setTextFormat(Qt.RichText)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setProperty("class", "rewriteBody")
        return label

    def _bullet_label(self, items: list[str]) -> QLabel:
        body = "<ul style='margin:0; padding-left:18px;'>" + "".join(
            f"<li style='margin-bottom:6px;'>{_esc(x)}</li>" for x in items
        ) + "</ul>"
        label = QLabel(body)
        label.setWordWrap(True)
        label.setTextFormat(Qt.RichText)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setProperty("class", "reportBody")
        return label

    def _collocation_label(self, items: list[Collocation]) -> QLabel:
        rows = []
        for item in items:
            if item.translation:
                rows.append(
                    "<li style='margin-bottom:8px;'>"
                    f"<b>{_esc(item.expression)}</b><br/>"
                    f"<span style='color:#667085;'>{_esc(item.translation)}</span>"
                    "</li>"
                )
            else:
                rows.append(f"<li style='margin-bottom:6px;'><b>{_esc(item.expression)}</b></li>")
        body = "<ul style='margin:0; padding-left:18px;'>" + "".join(rows) + "</ul>"
        label = QLabel(body)
        label.setWordWrap(True)
        label.setTextFormat(Qt.RichText)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setProperty("class", "reportBody")
        return label

    def _build_scores(self, result: GradingResult) -> QWidget:
        wrap = QWidget()
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        overall = QFrame()
        overall.setObjectName("overallCard")
        ov = QVBoxLayout(overall)
        ov.setContentsMargins(12, 10, 12, 10)
        ov.setSpacing(2)
        ol = QLabel("Overall")
        ol.setObjectName("overallLabel")
        ovv = QLabel(f"{result.overallBand:g}")
        ovv.setObjectName("overallValue")
        ov.addWidget(ol)
        ov.addWidget(ovv)
        row.addWidget(overall)

        by_label = {s.label: s for s in result.scores}
        first_label = "TA" if result.taskType == "task1" else "TR"
        for lbl in [first_label, "CC", "LR", "GRA"]:
            sc = by_label.get(lbl) or (by_label.get("TR/TA") if lbl == first_label else None)
            card = QFrame()
            card.setProperty("class", "scoreCard")
            cv = QVBoxLayout(card)
            cv.setContentsMargins(12, 10, 12, 10)
            cv.setSpacing(2)
            cl = QLabel(lbl)
            cl.setProperty("class", "scoreLabel")
            cvv = QLabel(f"{sc.score:g}" if sc else "—")
            cvv.setProperty("class", "scoreValue")
            cv.addWidget(cl)
            cv.addWidget(cvv)
            if sc and sc.rationale:
                card.setToolTip(sc.rationale)
            row.addWidget(card)

        return wrap

    def _build_score_diagnosis(self, diagnosis: ScoreDiagnosis) -> QFrame:
        wrap = QFrame()
        wrap.setProperty("class", "reportSection")
        wrap.setObjectName("scoreDiagnosisSection")
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(8)
        title = QLabel(
            f"01 · Score Diagnosis 分数诊断 · Target {diagnosis.targetBand:g} · "
            f"Gap {diagnosis.gapToTarget:g}"
        )
        title.setProperty("class", "reportSectionTitle")
        layout.addWidget(title)
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
            layout.addWidget(self._commentary_label(commentary))
        return wrap

    def _build_criterion_diagnosis(self, criterion) -> QFrame:
        card = QFrame()
        card.setProperty("class", "criterionCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(11, 9, 11, 11)
        layout.setSpacing(6)
        title = QLabel(f"{criterion.name} · {criterion.score:g}")
        title.setProperty("class", "criterionTitle")
        layout.addWidget(title)
        problems = criterion.mainProblemsEn + criterion.mainProblemsZh
        if problems:
            layout.addWidget(self._notice_box(
                "Main issue 主要扣分点",
                problems,
                "issueBox",
            ))
        next_steps = [
            text for text in (criterion.nextStepEn, criterion.nextStepZh) if text
        ]
        if next_steps:
            layout.addWidget(self._notice_box(
                "Score improvement 提分重点",
                next_steps,
                "suggestionBox",
            ))
        return card

    def _build_paragraph(self, pf: ParagraphFeedback) -> QFrame:
        block = QFrame()
        block.setProperty("class", "feedbackBlock")
        v = QVBoxLayout(block)
        v.setContentsMargins(12, 10, 12, 12)
        v.setSpacing(6)

        function = pf.function.strip().title()
        head = QLabel(
            f"Paragraph {pf.paragraph}" + (f" / {function}" if function else "")
        )
        head.setProperty("class", "paragraphTitle")
        v.addWidget(head)

        strengths = pf.strengthsEn[:1]
        issues = pf.issuesEn[:2]
        improvements = pf.howToImproveEn[:1]
        summary = QFrame()
        summary.setProperty("class", "feedbackSummary")
        summary_layout = QHBoxLayout(summary)
        summary_layout.setContentsMargins(0, 0, 0, 0)
        summary_layout.setSpacing(0)
        if strengths:
            summary_layout.addWidget(
                self._notice_box("Strength 优点", strengths, "strengthBox")
            )
        if issues:
            summary_layout.addWidget(
                self._notice_box("Issue 问题", issues, "issueBox")
            )
        if improvements:
            summary_layout.addWidget(self._notice_box(
                "Suggestion 建议",
                improvements,
                "suggestionBox",
            ))
        if summary_layout.count():
            v.addWidget(summary)
        if pf.sentenceUpgrade:
            original = pf.sentenceUpgrade.get("original", "")
            improved = pf.sentenceUpgrade.get("improved", "")
            if original or improved:
                sentence_title = QLabel("Sentence upgrade 句子升级")
                sentence_title.setProperty("class", "subsectionTitle")
                v.addWidget(sentence_title)
                pair = QFrame()
                pair.setProperty("class", "sentencePair")
                pair_layout = QHBoxLayout(pair)
                pair_layout.setContentsMargins(0, 0, 0, 0)
                pair_layout.setSpacing(0)
                pair_layout.addWidget(self._compare_label(
                    "Original 原句", original, "compareOriginal"
                ))
                pair_layout.addWidget(self._compare_label(
                    "Improved 改后句", improved, "compareImproved"
                ))
                v.addWidget(pair)
        return block

    def _build_syntax_upgrade(self, item: SyntaxUpgrade) -> QFrame:
        block = QFrame()
        block.setProperty("class", "sentenceCompare")
        v = QVBoxLayout(block)
        v.setContentsMargins(12, 10, 12, 12)
        v.setSpacing(7)

        head = QLabel(item.learningTitleZh)
        head.setProperty("class", "paragraphTitle")
        v.addWidget(head)

        for heading, text, css_class in (
            ("Original Sentence 原句", item.studentOriginalSentence, "compareOriginal"),
            ("Final Upgraded Sentence 最终升级句", item.upgradedSentence, "compareImproved"),
        ):
            if not text:
                continue
            v.addWidget(self._compare_label(heading, text, css_class))

        details = [
            f"为什么值得学：{item.learningReasonZh}" if item.learningReasonZh else "",
        ]
        details = [text for text in details if text]
        if details:
            v.addWidget(self._mini("Learning notes 学习说明", details))
        if item.howToReuseZh:
            v.addWidget(self._notice_box(
                "Transfer method 迁移方法",
                [item.howToReuseZh],
                "suggestionBox",
            ))
        return block

    def _build_decision_groups(
        self, items: list[ValidatorDecision]
    ) -> QWidget:
        wrap = QWidget()
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        for decision in ("KEEP", "SIMPLIFY", "REMOVE"):
            group = [item for item in items if item.decision.upper() == decision]
            if not group:
                continue
            heading = QLabel(f"{decision} · {len(group)}")
            heading.setProperty("class", f"decisionHeading {decision.lower()}")
            layout.addWidget(heading)
            for item in group:
                layout.addWidget(self._build_decision(item))
        unmatched = [
            item for item in items
            if item.decision.upper() not in {"KEEP", "SIMPLIFY", "REMOVE"}
        ]
        for item in unmatched:
            layout.addWidget(self._build_decision(item))
        return wrap

    def _build_decision(self, item: ValidatorDecision) -> QFrame:
        block = QFrame()
        block.setProperty("class", f"decisionCard {item.decision.lower()}")
        layout = QVBoxLayout(block)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(6)
        title = QLabel(item.item or item.deepseekVersion or item.finalVersion or "终审决定")
        title.setProperty("class", "paragraphTitle")
        title.setWordWrap(True)
        layout.addWidget(title)
        details = [
            f"Final choice: {item.finalVersion}" if item.finalVersion else "",
            f"Why: {item.reasonZh or item.reasonEn}"
            if (item.reasonZh or item.reasonEn) else "",
            f"Takeaway: {item.studentTakeawayZh or item.examAdviceZh}"
            if (item.studentTakeawayZh or item.examAdviceZh) else "",
        ]
        details = [text for text in details if text]
        if details:
            layout.addWidget(self._mini("终审结论", details))
        return block

    def _build_memorise_expression(
        self, item: MemoriseWorthyExpression
    ) -> QFrame:
        block = QFrame()
        block.setProperty("class", "memoryCard")
        layout = QVBoxLayout(block)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(6)
        title = QLabel(item.expression)
        title.setProperty("class", "paragraphTitle")
        title.setWordWrap(True)
        layout.addWidget(title)
        details = [
            item.meaningZh,
            f"Example: {item.exampleSentence}" if item.exampleSentence else "",
            item.whyUsefulZh,
        ]
        details = [text for text in details if text]
        if details:
            layout.addWidget(self._mini("使用说明", details))
        if item.warningZh:
            layout.addWidget(self._notice_box(
                "注意事项",
                [item.warningZh],
                "suggestionBox",
            ))
        return block

    def _compare_label(
        self,
        title: str,
        text: str,
        css_class: str,
    ) -> QLabel:
        label = QLabel(f"<b>{_esc(title)}</b><br/>{_esc(text or '—')}")
        label.setWordWrap(True)
        label.setTextFormat(Qt.RichText)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setProperty("class", css_class)
        return label

    def _notice_box(
        self,
        title: str,
        items: list[str],
        css_class: str,
    ) -> QLabel:
        body = f"<b>{_esc(title)}</b><br/>" + "<br/>".join(
            f"·&nbsp; {_esc(item)}" for item in items
        )
        label = QLabel(body)
        label.setWordWrap(True)
        label.setTextFormat(Qt.RichText)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setProperty("class", css_class)
        return label

    def _tag_label(self, text: str, css_class: str) -> QLabel:
        label = QLabel(_esc(text))
        label.setProperty("class", f"reportTag {css_class}")
        label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        return label

    def _practice_list(self, items: list[str]) -> QWidget:
        wrap = QWidget()
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        for index, item in enumerate(items, 1):
            layout.addWidget(self._notice_box(
                f"{index}. Action",
                [item],
                "suggestionBox",
            ))
        return wrap

    def _mini(self, title: str, items: list[str]) -> QLabel:
        body = f"<b>{_esc(title)}</b><br/>" + "<br/>".join(
            f"·&nbsp; {_esc(x)}" for x in items
        )
        return self._mini_label(body)

    def _commentary_label(self, paragraphs: list[str]) -> QLabel:
        body = "<b>Examiner Commentary 考官完整评语</b>" + "".join(
            f"<p style='margin:7px 0 0 0; line-height:1.55;'>{_esc(text)}</p>"
            for text in paragraphs
        )
        return self._mini_label(body)

    def _mini_repl(self, title: str, items: list[Replacement]) -> QLabel:
        lines = []
        for it in items:
            line = f"·&nbsp; <b>{_esc(it.original)}</b> → {_esc(it.improved)}"
            if it.reason:
                line += f" <span style='color:#8a8278;'>（{_esc(it.reason)}）</span>"
            lines.append(line)
        body = f"<b>{_esc(title)}</b><br/>" + "<br/>".join(lines)
        return self._mini_label(body)

    def _mini_label(self, html: str) -> QLabel:
        label = QLabel(html)
        label.setWordWrap(True)
        label.setTextFormat(Qt.RichText)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setProperty("class", "miniFeedback")
        return label

    def clear(self) -> None:
        self._show_empty()


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _paragraph_html(text: str) -> str:
    paragraphs = [p.strip() for p in text.splitlines() if p.strip()]
    if not paragraphs and text.strip():
        paragraphs = [text.strip()]
    return "".join(
        "<p style='margin:0 0 10px 0; line-height:1.55;'>" + _esc(p) + "</p>"
        for p in paragraphs
    )


def _recommendation_class(value: str) -> str:
    lowered = value.strip().lower()
    if "avoid" in lowered:
        return "tagRed"
    if "careful" in lowered:
        return "tagYellow"
    return "tagBlue"
