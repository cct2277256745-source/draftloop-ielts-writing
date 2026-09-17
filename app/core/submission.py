"""Immutable Candidate Script versions and original-text locators for Phase 2."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any


SUBMISSION_SCHEMA_VERSION = "submission-snapshot-v1"
PREPROCESSING_VERSION = "original-text-locators-v1"
_LINE_BREAKS = frozenset({"\n", "\r", "\u0085", "\u2028", "\u2029"})
_TERMINALS = frozenset({".", "?", "!", "。", "？", "！"})
_CLOSERS = frozenset({"'", '"', "”", "’", ")", "]", "}", "）", "】", "」", "』"})
_ABBREVIATIONS = frozenset({"e.g.", "i.e.", "etc.", "mr.", "mrs.", "ms.", "dr.", "prof.", "vs."})
_WORD_RE = re.compile(r"[^\W_]+(?:[’'-][^\W_]+)*|\d+(?:[.,]\d+)*", re.UNICODE)


class SubmissionError(ValueError):
    """The supplied task/question/Candidate Script cannot form a snapshot."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    encoded = value.encode("utf-8") if isinstance(value, str) else canonical_json(value).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalise_navigation(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


@dataclass(frozen=True)
class TextLocator:
    """One exact original-text range; offsets are Unicode code points, `[start, end)`."""

    locator_id: str
    start: int
    end: int
    source_text: str = field(repr=False, compare=False)
    normalized_text: str = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.locator_id or not isinstance(self.start, int) or not isinstance(self.end, int):
            raise SubmissionError("invalid locator")
        if self.start < 0 or self.end <= self.start or not self.source_text:
            raise SubmissionError("invalid locator bounds")

    def identity_payload(self) -> dict[str, Any]:
        return {
            "id": self.locator_id,
            "start": self.start,
            "end": self.end,
            "normalizedText": self.normalized_text,
        }


@dataclass(frozen=True)
class Paragraph:
    locator: TextLocator
    sentences: tuple[TextLocator, ...]

    def __post_init__(self) -> None:
        sentences = tuple(self.sentences)
        if not sentences:
            raise SubmissionError("paragraph needs a sentence")
        object.__setattr__(self, "sentences", sentences)

    def identity_payload(self) -> dict[str, Any]:
        return {
            "paragraph": self.locator.identity_payload(),
            "sentences": [sentence.identity_payload() for sentence in self.sentences],
        }


def _logical_lines(text: str) -> tuple[tuple[int, int], ...]:
    result: list[tuple[int, int]] = []
    start = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\r" and index + 1 < len(text) and text[index + 1] == "\n":
            result.append((start, index))
            index += 2
            start = index
        elif char in _LINE_BREAKS:
            result.append((start, index))
            index += 1
            start = index
        else:
            index += 1
    result.append((start, len(text)))
    return tuple(result)


def _is_terminal(text: str, sentence_start: int, index: int) -> bool:
    char = text[index]
    if char != ".":
        return char in _TERMINALS
    if index > sentence_start and index + 1 < len(text) and text[index - 1].isdigit() and text[index + 1].isdigit():
        return False
    probe = text[max(sentence_start, index - 16):index + 1].lower()
    return not any(probe.endswith(item) for item in _ABBREVIATIONS)


def _sentences(text: str, paragraph_start: int, paragraph_end: int, paragraph_id: str) -> tuple[TextLocator, ...]:
    result: list[TextLocator] = []
    cursor = paragraph_start
    ordinal = 1
    while cursor < paragraph_end:
        while cursor < paragraph_end and text[cursor].isspace():
            cursor += 1
        if cursor >= paragraph_end:
            break
        end = paragraph_end
        index = cursor
        while index < paragraph_end:
            if _is_terminal(text, cursor, index):
                end = index + 1
                while end < paragraph_end and text[end] in _TERMINALS:
                    end += 1
                while end < paragraph_end and text[end] in _CLOSERS:
                    end += 1
                break
            index += 1
        while end > cursor and text[end - 1].isspace():
            end -= 1
        if end <= cursor:
            break
        source = text[cursor:end]
        result.append(TextLocator(
            f"{paragraph_id}-s{ordinal:04d}", cursor, end, source, _normalise_navigation(source)
        ))
        ordinal += 1
        cursor = end
    if not result:
        raise SubmissionError("paragraph segmentation failed")
    return tuple(result)


def _paragraphs(text: str) -> tuple[Paragraph, ...]:
    result: list[Paragraph] = []
    group_start: int | None = None
    group_end: int | None = None
    for start, end in _logical_lines(text):
        if text[start:end].strip():
            if group_start is None:
                group_start = start
            group_end = end
        elif group_start is not None and group_end is not None:
            ordinal = len(result) + 1
            paragraph_id = f"p{ordinal:04d}"
            source = text[group_start:group_end]
            locator = TextLocator(paragraph_id, group_start, group_end, source, _normalise_navigation(source))
            result.append(Paragraph(locator, _sentences(text, group_start, group_end, paragraph_id)))
            group_start = group_end = None
    if group_start is not None and group_end is not None:
        ordinal = len(result) + 1
        paragraph_id = f"p{ordinal:04d}"
        source = text[group_start:group_end]
        locator = TextLocator(paragraph_id, group_start, group_end, source, _normalise_navigation(source))
        result.append(Paragraph(locator, _sentences(text, group_start, group_end, paragraph_id)))
    if not result:
        raise SubmissionError("Candidate Script is blank")
    return tuple(result)


@dataclass(frozen=True)
class EssayVersion:
    """Exact immutable Candidate Script text plus a deterministic locator manifest."""

    schema_version: str
    preprocessing_version: str
    essay_version_id: str
    content_sha256: str
    original_text: str = field(repr=False, compare=False)
    paragraphs: tuple[Paragraph, ...] = field(repr=False, compare=False)
    word_count: int = 0
    locator_manifest_sha256: str = ""

    @classmethod
    def create(cls, original_text: str, *, preprocessing_version: str = PREPROCESSING_VERSION) -> "EssayVersion":
        if not isinstance(original_text, str) or not original_text.strip():
            raise SubmissionError("Candidate Script is blank")
        if not isinstance(preprocessing_version, str) or not preprocessing_version.strip():
            raise SubmissionError("preprocessing version is required")
        content_sha256 = digest(original_text)
        paragraphs = _paragraphs(original_text)
        word_count = len(_WORD_RE.findall(original_text))
        locator_payload = {
            "preprocessingVersion": preprocessing_version,
            "paragraphs": [paragraph.identity_payload() for paragraph in paragraphs],
            "wordCount": word_count,
        }
        return cls(
            schema_version=SUBMISSION_SCHEMA_VERSION,
            preprocessing_version=preprocessing_version,
            essay_version_id=f"essay:{content_sha256}",
            content_sha256=content_sha256,
            original_text=original_text,
            paragraphs=paragraphs,
            word_count=word_count,
            locator_manifest_sha256=digest(locator_payload),
        )

    def sentence_ids(self) -> frozenset[str]:
        return frozenset(sentence.locator_id for paragraph in self.paragraphs for sentence in paragraph.sentences)

    def paragraph_ids(self) -> frozenset[str]:
        return frozenset(paragraph.locator.locator_id for paragraph in self.paragraphs)

    def locator_manifest(self) -> dict[str, Any]:
        return {
            "essayVersionId": self.essay_version_id,
            "contentSha256": self.content_sha256,
            "preprocessingVersion": self.preprocessing_version,
            "locatorManifestSha256": self.locator_manifest_sha256,
            "wordCount": self.word_count,
            "paragraphs": [paragraph.identity_payload() for paragraph in self.paragraphs],
        }


@dataclass(frozen=True)
class SubmissionSnapshot:
    """Content-addressed Task intake. Raw fields do not enter log-safe projections."""

    schema_version: str
    submission_snapshot_id: str
    snapshot_sha256: str
    task_type: str
    question: str = field(repr=False, compare=False)
    question_content_sha256: str = ""
    essay_version: EssayVersion = field(default=None, repr=False, compare=False)  # type: ignore[assignment]

    @classmethod
    def create(cls, task_type: str, question: str, essay: str) -> "SubmissionSnapshot":
        if task_type != "task2":
            raise SubmissionError("P2-01 only accepts Task 2")
        if not isinstance(question, str) or not question.strip():
            raise SubmissionError("Task question is blank")
        essay_version = EssayVersion.create(essay)
        question_sha256 = digest(question)
        identity = {
            "schemaVersion": SUBMISSION_SCHEMA_VERSION,
            "taskType": task_type,
            "questionContentSha256": question_sha256,
            "essayVersionId": essay_version.essay_version_id,
        }
        snapshot_sha256 = digest(identity)
        return cls(
            schema_version=SUBMISSION_SCHEMA_VERSION,
            submission_snapshot_id=f"submission:{snapshot_sha256}",
            snapshot_sha256=snapshot_sha256,
            task_type=task_type,
            question=question,
            question_content_sha256=question_sha256,
            essay_version=essay_version,
        )

    @classmethod
    def from_request(cls, request: Any) -> "SubmissionSnapshot":
        return cls.create(request.taskType, request.question, request.essay)

    def log_safe_lineage(self) -> dict[str, str]:
        return {
            "submissionSnapshotId": self.submission_snapshot_id,
            "submissionSnapshotSha256": self.snapshot_sha256,
            "questionContentSha256": self.question_content_sha256,
            "essayVersionId": self.essay_version.essay_version_id,
            "essayContentSha256": self.essay_version.content_sha256,
            "locatorManifestSha256": self.essay_version.locator_manifest_sha256,
        }
