"""数据模型：与批改 JSON 结构一一对应的 dataclass。

LLM 返回 JSON -> from_dict 解析为强类型对象，供 UI 与 PDF 导出使用。
字段命名沿用既有 schema(TR/TA, CC, LR, GRA 四项)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

TaskType = str  # "task1" | "task2"


@dataclass
class CorpusReference:
    id: str
    taskType: str
    question: str
    chartNote: str
    modelEssay: str
    topicTags: list[str]
    collocations: list[str]
    band: float = 8.0
    source: str = ""

    @staticmethod
    def from_dict(d: Any) -> "CorpusReference":
        d = _as_dict(d)
        return CorpusReference(
            id=d.get("id", ""),
            taskType=d.get("taskType", "task2"),
            question=d.get("question", ""),
            chartNote=d.get("chartNote", ""),
            modelEssay=d.get("modelEssay", ""),
            topicTags=list(d.get("topicTags", [])),
            collocations=list(d.get("collocations", [])),
            band=float(d.get("band", 8.0)),
            source=d.get("source", ""),
        )


@dataclass
class BandScore:
    label: str  # "TR/TA" | "CC" | "LR" | "GRA"
    score: float
    rationale: str = ""

    @staticmethod
    def from_dict(d: Any) -> "BandScore":
        d = _as_dict(d)
        return BandScore(
            label=str(d.get("label", "")),
            score=_to_float(d.get("score", 0)),
            rationale=str(d.get("rationale", "")),
        )


@dataclass
class CriterionDiagnosis:
    name: str = ""
    score: float = 0.0
    commentEn: str = ""
    commentZh: str = ""
    mainProblemsEn: list[str] = field(default_factory=list)
    mainProblemsZh: list[str] = field(default_factory=list)
    nextStepEn: str = ""
    nextStepZh: str = ""

    @staticmethod
    def from_dict(d: Any) -> "CriterionDiagnosis":
        d = _as_dict(d)
        return CriterionDiagnosis(
            name=str(d.get("name", "")),
            score=_to_float(d.get("score", 0)),
            commentEn=str(d.get("commentEn", d.get("comment", ""))),
            commentZh=str(d.get("commentZh", "")),
            mainProblemsEn=_string_list(d.get("mainProblemsEn")),
            mainProblemsZh=_string_list(d.get("mainProblemsZh")),
            nextStepEn=str(d.get("nextStepEn", "")),
            nextStepZh=str(d.get("nextStepZh", "")),
        )


@dataclass
class ScoreDiagnosis:
    overall: float = 0.0
    targetBand: float = 0.0
    gapToTarget: float = 0.0
    currentLevelSummaryEn: str = ""
    currentLevelSummaryZh: str = ""
    whyThisScoreEn: str = ""
    whyThisScoreZh: str = ""
    targetGapAnalysisEn: str = ""
    targetGapAnalysisZh: str = ""
    criteria: list[CriterionDiagnosis] = field(default_factory=list)

    @staticmethod
    def from_dict(d: Any) -> "ScoreDiagnosis":
        d = _as_dict(d)
        return ScoreDiagnosis(
            overall=_to_float(d.get("overall", 0)),
            targetBand=_to_float(d.get("targetBand", 0)),
            gapToTarget=_to_float(d.get("gapToTarget", 0)),
            currentLevelSummaryEn=str(d.get("currentLevelSummaryEn", "")),
            currentLevelSummaryZh=str(d.get("currentLevelSummaryZh", "")),
            whyThisScoreEn=str(d.get("whyThisScoreEn", "")),
            whyThisScoreZh=str(d.get("whyThisScoreZh", "")),
            targetGapAnalysisEn=str(d.get("targetGapAnalysisEn", "")),
            targetGapAnalysisZh=str(d.get("targetGapAnalysisZh", "")),
            criteria=[
                CriterionDiagnosis.from_dict(x)
                for x in _as_list(d.get("criteria"))
            ],
        )


@dataclass
class Replacement:
    original: str
    improved: str
    reason: str = ""

    @staticmethod
    def from_dict(d: Any, improved_key: str = "improved") -> "Replacement":
        if not isinstance(d, dict):
            return Replacement(original=str(d))
        return Replacement(
            original=str(d.get("original", "")),
            improved=str(d.get(improved_key, d.get("improved", d.get("corrected", "")))),
            reason=str(d.get("reason", "")),
        )


@dataclass
class TargetRewrite:
    band: float
    label: str
    essay: str
    focus: str = ""

    @staticmethod
    def from_dict(d: Any) -> "TargetRewrite":
        if not isinstance(d, dict):
            return TargetRewrite(band=0, label="", essay=str(d))
        return TargetRewrite(
            band=_to_float(d.get("band", 0)),
            label=str(d.get("label", "")),
            essay=str(d.get("essay", "")),
            focus=str(d.get("focus", "")),
        )


@dataclass
class SentenceComparison:
    paragraph: int
    original: str
    improved: str
    reason: str = ""

    @staticmethod
    def from_dict(d: Any) -> "SentenceComparison":
        if not isinstance(d, dict):
            return SentenceComparison(paragraph=0, original="", improved=str(d))
        return SentenceComparison(
            paragraph=int(_to_float(d.get("paragraph", 0))),
            original=str(d.get("original", "")),
            improved=str(d.get("improved", "")),
            reason=str(d.get("reason", "")),
        )


@dataclass
class SyntaxUpgrade:
    title: str = ""
    syntaxTypeZh: str = ""
    studentOriginalSentence: str = ""
    finalUpgradedSentence: str = ""
    qwenExamReadySentence: str = ""
    deepseekSyntaxEnhancedSentence: str = ""
    syntaxPattern: str = ""
    whyItImprovesGRAEn: str = ""
    whyItImprovesGRAZh: str = ""
    whyWorthLearningZh: str = ""
    examUsability: str = ""
    difficulty: str = ""
    targetBandSuitability: str = ""
    naturalness: str = ""
    howToReuseZh: str = ""
    warningZh: str = ""

    @staticmethod
    def from_dict(d: Any) -> "SyntaxUpgrade":
        if not isinstance(d, dict):
            return SyntaxUpgrade(deepseekSyntaxEnhancedSentence=str(d))
        return SyntaxUpgrade(
            title=str(d.get("title", "")),
            syntaxTypeZh=str(d.get("syntaxTypeZh", d.get("title", ""))),
            studentOriginalSentence=str(d.get(
                "studentOriginalSentence",
                d.get("originalSentence", d.get("original", "")),
            )),
            finalUpgradedSentence=str(d.get(
                "finalUpgradedSentence",
                d.get(
                    "deepseekSyntaxEnhancedSentence",
                    d.get("upgradedSentence", d.get("improved", "")),
                ),
            )),
            qwenExamReadySentence=str(d.get("qwenExamReadySentence", "")),
            deepseekSyntaxEnhancedSentence=str(d.get(
                "deepseekSyntaxEnhancedSentence",
                d.get(
                    "finalUpgradedSentence",
                    d.get("upgradedSentence", d.get("improved", "")),
                ),
            )),
            syntaxPattern=str(d.get("syntaxPattern", "")),
            whyItImprovesGRAEn=str(d.get(
                "whyItImprovesGRAEn",
                d.get("whyItImprovesGRA", d.get("reason", "")),
            )),
            whyItImprovesGRAZh=str(d.get("whyItImprovesGRAZh", "")),
            whyWorthLearningZh=str(d.get(
                "whyWorthLearningZh",
                d.get("whyItImprovesGRAZh", ""),
            )),
            examUsability=str(d.get("examUsability", "")),
            difficulty=str(d.get("difficulty", "")),
            targetBandSuitability=str(d.get("targetBandSuitability", "")),
            naturalness=str(d.get("naturalness", "")),
            howToReuseZh=str(d.get("howToReuseZh", "")),
            warningZh=str(d.get("warningZh", "")),
        )

    @property
    def originalSentence(self) -> str:
        return self.studentOriginalSentence

    @property
    def upgradedSentence(self) -> str:
        return self.finalUpgradedSentence or self.deepseekSyntaxEnhancedSentence

    @property
    def learningTitleZh(self) -> str:
        return self.syntaxTypeZh or self.title or "句法升级"

    @property
    def learningReasonZh(self) -> str:
        return self.whyWorthLearningZh or self.whyItImprovesGRAZh

    @property
    def whyItImprovesGRA(self) -> str:
        return self.whyItImprovesGRAEn


@dataclass
class VocabularyUpgrade:
    originalExpression: str = ""
    suggestedExpression: str = ""
    meaningZh: str = ""
    whyBetterEn: str = ""
    whyBetterZh: str = ""
    difficulty: str = ""
    examUsability: str = ""
    naturalness: str = ""
    targetBandSuitability: str = ""
    recommendation: str = ""

    @staticmethod
    def from_dict(d: Any) -> "VocabularyUpgrade":
        if not isinstance(d, dict):
            return VocabularyUpgrade(suggestedExpression=str(d))
        return VocabularyUpgrade(
            originalExpression=str(d.get("originalExpression", d.get("original", ""))),
            suggestedExpression=str(d.get("suggestedExpression", d.get("improved", ""))),
            meaningZh=str(d.get("meaningZh", "")),
            whyBetterEn=str(d.get("whyBetterEn", d.get("why", d.get("reason", "")))),
            whyBetterZh=str(d.get("whyBetterZh", "")),
            difficulty=str(d.get("difficulty", "")),
            examUsability=str(d.get("examUsability", "")),
            naturalness=str(d.get("naturalness", "")),
            targetBandSuitability=str(d.get("targetBandSuitability", "")),
            recommendation=str(d.get("recommendation", "")),
        )

    @property
    def why(self) -> str:
        return self.whyBetterEn


@dataclass
class Collocation:
    expression: str
    translation: str = ""

    @staticmethod
    def from_value(value: Any) -> "Collocation":
        if isinstance(value, dict):
            return Collocation(
                expression=str(value.get("expression", value.get("text", ""))),
                translation=str(value.get("translation", value.get("chinese", ""))),
            )
        return Collocation(expression=str(value))


@dataclass
class ParagraphFeedback:
    paragraph: int
    function: str = ""
    estimatedBandImpact: str = ""
    strengthsEn: list[str] = field(default_factory=list)
    strengthsZh: list[str] = field(default_factory=list)
    issuesEn: list[str] = field(default_factory=list)
    issuesZh: list[str] = field(default_factory=list)
    howToImproveEn: list[str] = field(default_factory=list)
    howToImproveZh: list[str] = field(default_factory=list)
    sentenceUpgrade: dict[str, str] = field(default_factory=dict)
    vocabularyUpgrades: list[Replacement] = field(default_factory=list)
    grammarCorrections: list[Replacement] = field(default_factory=list)
    sentenceUpgrades: list[Replacement] = field(default_factory=list)

    @staticmethod
    def from_dict(d: Any) -> "ParagraphFeedback":
        if not isinstance(d, dict):
            return ParagraphFeedback(paragraph=0, issuesEn=[str(d)])
        return ParagraphFeedback(
            paragraph=int(_to_float(d.get("paragraphNumber", d.get("paragraph", 0)))),
            function=str(d.get("function", "")),
            estimatedBandImpact=str(d.get("estimatedBandImpact", "")),
            strengthsEn=_string_list(d.get("strengthsEn", d.get("strengths"))),
            strengthsZh=_string_list(d.get("strengthsZh")),
            issuesEn=_string_list(d.get("issuesEn", d.get("issues"))),
            issuesZh=_string_list(d.get("issuesZh")),
            howToImproveEn=_string_list(d.get("howToImproveEn")),
            howToImproveZh=_string_list(d.get("howToImproveZh")),
            sentenceUpgrade=_sentence_upgrade(d.get("sentenceUpgrade")),
            vocabularyUpgrades=[
                Replacement.from_dict(x) for x in _as_list(d.get("vocabularyUpgrades"))
            ],
            grammarCorrections=[
                Replacement.from_dict(x, "corrected")
                for x in _as_list(d.get("grammarCorrections"))
            ],
            sentenceUpgrades=[
                Replacement.from_dict(x) for x in _as_list(d.get("sentenceUpgrades"))
            ],
        )

    @property
    def strengths(self) -> list[str]:
        return self.strengthsEn

    @property
    def issues(self) -> list[str]:
        return self.issuesEn


@dataclass
class MemoriseWorthyExpression:
    expression: str = ""
    meaningZh: str = ""
    topic: str = ""
    exampleSentence: str = ""
    whyUsefulZh: str = ""
    reusability: str = ""
    targetBandSuitability: str = ""
    warningZh: str = ""

    @staticmethod
    def from_dict(d: Any) -> "MemoriseWorthyExpression":
        if not isinstance(d, dict):
            return MemoriseWorthyExpression(expression=str(d))
        return MemoriseWorthyExpression(
            expression=str(d.get("expression", d.get("text", ""))),
            meaningZh=str(d.get("meaningZh", d.get("translation", ""))),
            topic=str(d.get("topic", "")),
            exampleSentence=str(d.get("exampleSentence", "")),
            whyUsefulZh=str(d.get("whyUsefulZh", "")),
            reusability=str(d.get("reusability", "")),
            targetBandSuitability=str(d.get("targetBandSuitability", "")),
            warningZh=str(d.get("warningZh", "")),
        )


@dataclass
class ValidatorDecision:
    decision: str = ""
    item: str = ""
    deepseekVersion: str = ""
    finalVersion: str = ""
    reasonEn: str = ""
    reasonZh: str = ""
    studentTakeawayZh: str = ""
    examAdviceZh: str = ""
    naturalnessIssue: str = ""
    targetBandSuitability: str = ""

    @staticmethod
    def from_dict(d: Any) -> "ValidatorDecision":
        if not isinstance(d, dict):
            return ValidatorDecision(item=str(d))
        return ValidatorDecision(
            decision=str(d.get("decision", "")).upper(),
            item=str(d.get("item", d.get("change", d.get("upgradedSentence", "")))),
            deepseekVersion=str(d.get("deepseekVersion", "")),
            finalVersion=str(d.get("finalVersion", "")),
            reasonEn=str(d.get("reasonEn", d.get("reason", ""))),
            reasonZh=str(d.get("reasonZh", "")),
            studentTakeawayZh=str(d.get("studentTakeawayZh", "")),
            examAdviceZh=str(d.get("examAdviceZh", "")),
            naturalnessIssue=str(d.get("naturalnessIssue", "")),
            targetBandSuitability=str(d.get("targetBandSuitability", "")),
        )

    def __getitem__(self, key: str) -> str:
        if key == "change":
            return self.item
        return str(getattr(self, key))


@dataclass
class GradingResult:
    taskType: str = "task2"
    overallBand: float = 0.0
    summary: str = ""
    scores: list[BandScore] = field(default_factory=list)
    scoreDiagnosis: ScoreDiagnosis = field(default_factory=ScoreDiagnosis)
    paragraphFeedback: list[ParagraphFeedback] = field(default_factory=list)
    rewrittenEssay: str = ""
    targetRewrites: list[TargetRewrite] = field(default_factory=list)
    sentenceComparisons: list[SentenceComparison] = field(default_factory=list)
    brainstormingIdeas: list[str] = field(default_factory=list)
    collocations: list[Collocation] = field(default_factory=list)
    examinerWarnings: list[str] = field(default_factory=list)
    chartUnderstanding: dict[str, Any] = field(default_factory=dict)
    dataAccuracyCheck: dict[str, Any] = field(default_factory=dict)
    overviewCheck: dict[str, Any] = field(default_factory=dict)
    vocabularyUpgrades: list[VocabularyUpgrade] = field(default_factory=list)
    syntaxUpgrades: list[SyntaxUpgrade] = field(default_factory=list)
    qwenExamReadyVersion: str = ""
    deepseekSyntaxEnhancedVersion: str = ""
    balancedFinalVersion: str = ""
    balancedVersionQualityCheck: dict[str, Any] = field(default_factory=dict)
    deepseekChangesReview: list[ValidatorDecision] = field(default_factory=list)
    memoriseWorthyExpressions: list[MemoriseWorthyExpression] = field(default_factory=list)
    nextPracticeSuggestions: list[str] = field(default_factory=list)
    rawModelResponses: dict[str, str] = field(default_factory=dict)
    rubric_id: str = ""
    rubric_version: str = ""
    runtime_content_sha256: str = ""

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "GradingResult":
        return GradingResult(
            taskType=str(d.get("taskType", "task2")),
            overallBand=_to_float(
                d.get("overallBand", d.get("scores", {}).get("overall", 0)
                      if isinstance(d.get("scores"), dict) else 0)
            ),
            summary=str(d.get("summary", "")),
            scores=_parse_scores(d),
            scoreDiagnosis=ScoreDiagnosis.from_dict(d.get("scoreDiagnosis")),
            paragraphFeedback=[
                ParagraphFeedback.from_dict(x)
                for x in _as_list(d.get("paragraphFeedback"))
            ],
            rewrittenEssay=str(d.get("rewrittenEssay", "")),
            targetRewrites=[
                TargetRewrite.from_dict(x) for x in _as_list(d.get("targetRewrites"))
            ],
            sentenceComparisons=[
                SentenceComparison.from_dict(x)
                for x in _as_list(d.get("sentenceComparisons"))
            ],
            brainstormingIdeas=[str(x) for x in _as_list(d.get("brainstormingIdeas"))],
            collocations=[
                Collocation.from_value(x) for x in _as_list(d.get("collocations"))
            ],
            examinerWarnings=[str(x) for x in _as_list(d.get("examinerWarnings"))],
            chartUnderstanding=_as_section_dict(d.get("chartUnderstanding")),
            dataAccuracyCheck=_as_section_dict(d.get("dataAccuracyCheck")),
            overviewCheck=_as_section_dict(d.get("overviewCheck")),
            vocabularyUpgrades=[
                VocabularyUpgrade.from_dict(x)
                for x in _as_list(d.get("vocabularyUpgrades"))
            ],
            syntaxUpgrades=[
                SyntaxUpgrade.from_dict(x) for x in _as_list(d.get("syntaxUpgrades"))
            ],
            qwenExamReadyVersion=str(d.get("qwenExamReadyVersion", d.get("rewrittenEssay", ""))),
            deepseekSyntaxEnhancedVersion=str(d.get("deepseekSyntaxEnhancedVersion", "")),
            balancedFinalVersion=str(d.get("balancedFinalVersion", d.get("rewrittenEssay", ""))),
            balancedVersionQualityCheck=_as_section_dict(
                d.get("balancedVersionQualityCheck")
            ),
            deepseekChangesReview=[
                ValidatorDecision.from_dict(x)
                for x in _as_list(d.get("deepseekChangesReview"))
            ],
            memoriseWorthyExpressions=[
                MemoriseWorthyExpression.from_dict(x)
                for x in _as_list(d.get("memoriseWorthyExpressions"))
            ],
            nextPracticeSuggestions=[
                str(x) for x in _as_list(d.get("nextPracticeSuggestions"))
            ],
            rawModelResponses=_string_dict(d.get("rawModelResponses")),
            rubric_id=str(d.get("rubric_id", "")),
            rubric_version=str(d.get("rubric_version", "")),
            runtime_content_sha256=str(d.get("runtime_content_sha256", "")),
        )


@dataclass
class GradingRequest:
    taskType: str
    question: str
    essay: str
    targetBand: float = 7.5
    references: list[CorpusReference] = field(default_factory=list)
    imagePath: str = ""


def _parse_scores(d: dict[str, Any]) -> list[BandScore]:
    raw = d.get("scores", [])
    if isinstance(raw, list):
        return [BandScore.from_dict(x) for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        task_label = "TA" if d.get("taskType") == "task1" else "TR"
        return [
            BandScore(
                task_label,
                _to_float(raw.get("tr_ta", raw.get("ta_or_tr", raw.get(task_label.lower(), 0)))),
            ),
            BandScore("CC", _to_float(raw.get("cc", 0))),
            BandScore("LR", _to_float(raw.get("lr", 0))),
            BandScore("GRA", _to_float(raw.get("gra", 0))),
        ]
    return []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _as_section_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value in (None, ""):
        return {}
    return {"details": value}


def _string_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    if value in (None, ""):
        return {}
    return {"modelResponse": str(value)}


def _string_list(value: Any) -> list[str]:
    return [str(x) for x in _as_list(value) if str(x).strip()]


def _sentence_upgrade(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        if value in (None, ""):
            return {}
        return {"original": "", "improved": str(value), "whyEn": "", "whyZh": ""}
    return {
        "original": str(value.get("original", "")),
        "improved": str(value.get("improved", "")),
        "whyEn": str(value.get("whyEn", value.get("why", ""))),
        "whyZh": str(value.get("whyZh", "")),
    }


def _to_float(value: Any) -> float:
    """容错地把模型返回的分数(可能是 '7.5' / 7 / '7') 转成 float。"""
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (ValueError, TypeError):
        return 0.0
