"""批改 Prompt 构建：Task 1 保留旧路径，Task 2 要求显式 rubric snapshot。

强制 LLM 以「前任雅思考官」视角，按 TR/TA、CC、LR、GRA 四项独立打分，
逐段给修改建议，输出按目标 Band 分层的重写、逐句对照修改，并补充话题 ideas 与高级词伙；
返回严格的 JSON。检索到的内置范文作为评分锚点一并提供。
"""
from __future__ import annotations

import json
from pathlib import Path

from .models import CorpusReference, GradingRequest
from .rubric import StructuredRubricSnapshot, is_approved_task2_snapshot

SYSTEM_PROMPT = """You are a former IELTS examiner and senior academic writing coach with over a decade of experience marking IELTS Writing scripts against the official public band descriptors.

Grade the candidate's response with strict examiner discipline. Be fair but do not inflate scores: fluent yet underdeveloped, memorised, or off-topic writing must be marked down explicitly.

Scoring rules:
1. Score four criteria INDEPENDENTLY on the 0–9 scale, using half-bands (e.g. 6.5) where justified:
   - TR/TA (Task Response / Task Achievement)
   - CC (Coherence and Cohesion)
   - LR (Lexical Resource)
   - GRA (Grammatical Range and Accuracy)
2. The overall band is the average of the four, rounded to the nearest half-band (round .25 up to .5 and .75 up to the next whole band, per IELTS convention).
3. For Task 1, judge: accurate overview, key features selected, data/comparisons reported correctly, no opinion. For Task 2: clear position throughout, fully developed and relevant ideas, logical progression, range and accuracy of language.
4. Give paragraph-by-paragraph feedback. For EACH paragraph identify concrete strengths and issues, then provide:
   - vocabularyUpgrades: replace weak/imprecise words with more precise, higher-level alternatives (quote the candidate's original wording).
   - grammarCorrections: fix real errors (quote the original, give the corrected form).
   - sentenceUpgrades: rewrite clumsy sentences into more natural, sophisticated structures.
   Only include items that genuinely apply; do not invent errors.
5. targetRewrites: provide exactly ONE complete whole-essay rewrite at the user's selected target band (Band 7.5, 8.0, or 8.5), preserving the candidate's core ideas. Keep it exam-realistic and close to IELTS length. For Band 7.5, make it strong, clear, and achievable with controlled high-level language. For Band 8.0, make it precise, fully developed, and natural. For Band 8.5, make it highly polished, nuanced, and examiner-realistic without sounding memorised or over-ornate.
6. rewrittenEssay: set this to the same essay as the single targetRewrites item for backward compatibility. Do not generate a second rewrite.
7. sentenceComparisons: provide 6–10 high-value sentence-level comparisons. Quote the candidate's original sentence or clause, give an improved version, and explain the learning point briefly. Spread items across paragraphs where possible.
8. brainstormingIdeas: 4–7 strong, specific ideas/arguments a candidate could use for this topic.
9. collocations: 5–10 idiomatic, high-level collocations or sentence frames relevant to the topic. For each item, provide the English expression and a concise Simplified Chinese translation/explanation.
10. examinerWarnings: state plainly if the essay is under length, off-topic, memorised, lacks a clear position/overview, or otherwise penalised. Empty array if none.

Use the supplied internal high-score model essays only as a calibration reference for what a band 8 answer looks like on similar topics. NEVER copy them and never reveal their existence to the candidate.

Output ONLY a single valid JSON object (no markdown fences, no commentary) matching exactly:
{
  "overallBand": number,
  "summary": string,
  "scores": [{"label": "TR/TA"|"CC"|"LR"|"GRA", "score": number, "rationale": string}],
  "paragraphFeedback": [{
    "paragraph": number,
    "strengths": string[],
    "issues": string[],
    "vocabularyUpgrades": [{"original": string, "improved": string, "reason": string}],
    "grammarCorrections": [{"original": string, "corrected": string, "reason": string}],
    "sentenceUpgrades": [{"original": string, "improved": string}]
  }],
  "rewrittenEssay": string,
  "targetRewrites": [{"band": number, "label": string, "essay": string, "focus": string}],
  "sentenceComparisons": [{"paragraph": number, "original": string, "improved": string, "reason": string}],
  "brainstormingIdeas": string[],
  "collocations": [{"expression": string, "translation": string}],
  "examinerWarnings": string[]
}
All four scores must be present, in the order TR/TA, CC, LR, GRA."""


def build_system_prompt(
    task_type: str,
    rubric_snapshot: StructuredRubricSnapshot | None = None,
) -> str:
    """Select the legacy Task 1 prompt or the snapshot-gated Task 2 prompt."""
    if task_type == "task2":
        if not is_approved_task2_snapshot(rubric_snapshot):
            raise ValueError("Task 2 rubric snapshot is required.")
        return (
            Path(__file__).resolve().parents[1]
            / "prompts"
            / "qwen_main_review_task2.md"
        ).read_text(encoding="utf-8")
    return SYSTEM_PROMPT


def _render_reference(ref: CorpusReference, idx: int) -> str:
    parts = [
        f"--- Reference model essay {idx + 1} (band ~{ref.band:g}) ---",
        f"Question: {ref.question}",
    ]
    if ref.chartNote:
        parts.append(f"Note: {ref.chartNote}")
    parts.append(f"Model essay:\n{ref.modelEssay}")
    if ref.collocations:
        parts.append(f"Useful collocations: {'; '.join(ref.collocations)}")
    return "\n".join(parts)


def build_user_prompt(
    request: GradingRequest,
    rubric_snapshot: StructuredRubricSnapshot | None = None,
) -> str:
    if request.taskType == "task2":
        if not is_approved_task2_snapshot(rubric_snapshot):
            raise ValueError("Task 2 rubric snapshot is required.")
        payload = rubric_snapshot.to_prompt_payload()
        payload.update({
            "taskType": "task2",
            "question": request.question.strip(),
            "candidateEssay": request.essay.strip(),
            "targetBand": request.targetBand,
            "targetBandPurpose": "rewrite_only",
        })
        return json.dumps(payload, ensure_ascii=False)

    task_label = "Task 1 (Academic report)" if request.taskType == "task1" else "Task 2 (Argumentative essay)"

    if request.references:
        refs = "\n\n".join(_render_reference(r, i) for i, r in enumerate(request.references))
        ref_block = (
            "Internal high-score model essays on related topics (calibration only — do not copy or mention):\n\n"
            + refs
        )
    else:
        ref_block = "No internal reference essays matched this topic. Apply official IELTS band descriptors directly."

    return "\n\n".join([
        f"Task type: {task_label}",
        f"Selected target rewrite band: Band {request.targetBand:g}",
        f"Question:\n{request.question.strip()}",
        f"Candidate essay:\n{request.essay.strip()}",
        ref_block,
        "Now mark this script and return the JSON grading result described in your instructions.",
    ])
