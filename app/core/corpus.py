"""内置范文语料的加载与检索。

samples.json 由 scripts/build_corpus.py 从《雅思高分范文》逐页转写生成。
检索策略：按 taskType 过滤后，用题目关键词与每篇范文的
question/topicTags 做词重叠打分，取 top-k 注入批改 Prompt 作为评分基准。
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from .models import CorpusReference

SAMPLES_PATH = Path(__file__).resolve().parent.parent / "resources" / "corpus" / "samples.json"

# 停用词：参与匹配会带来噪声
_STOP = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for", "with",
    "that", "this", "these", "those", "is", "are", "be", "been", "should", "would",
    "some", "people", "think", "others", "believe", "view", "views", "give", "your",
    "own", "opinion", "discuss", "both", "what", "extent", "do", "you", "agree",
    "disagree", "below", "shows", "show", "summarise", "summarize", "information",
    "selecting", "reporting", "main", "features", "make", "comparisons", "where",
    "relevant", "graph", "chart", "table", "diagram", "following", "many", "their",
}


@lru_cache(maxsize=1)
def _all_references() -> tuple[CorpusReference, ...]:
    if not SAMPLES_PATH.exists():
        return ()
    try:
        data = json.loads(SAMPLES_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ()
    return tuple(CorpusReference.from_dict(d) for d in data)


def corpus_size() -> int:
    return len(_all_references())


def corpus_counts() -> tuple[int, int]:
    """返回 (task1 篇数, task2 篇数)。"""
    refs = _all_references()
    t1 = sum(1 for r in refs if r.taskType == "task1")
    return t1, len(refs) - t1


def _tokens(text: str) -> set[str]:
    words = re.split(r"[^a-z]+", text.lower())
    return {w for w in words if len(w) > 3 and w not in _STOP}


def find_references(task_type: str, question: str, top_k: int = 2) -> list[CorpusReference]:
    """按话题相关度返回最匹配的若干篇同类范文。

    无题目或无匹配时，回退为该类目的前 top_k 篇，保证 Prompt 总有基准范文可用。
    """
    refs = [r for r in _all_references() if r.taskType == task_type]
    if not refs:
        return []

    q_tokens = _tokens(question)
    if not q_tokens:
        return refs[:top_k]

    scored: list[tuple[int, CorpusReference]] = []
    for r in refs:
        haystack = _tokens(r.question) | {t.lower() for t in r.topicTags}
        overlap = len(q_tokens & haystack)
        scored.append((overlap, r))

    scored.sort(key=lambda x: x[0], reverse=True)
    best = [r for score, r in scored if score > 0][:top_k]
    return best if best else refs[:top_k]
