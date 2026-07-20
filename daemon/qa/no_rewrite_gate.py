#!/usr/bin/env python3
"""Content-preservation gates for Open Dictate."""
from __future__ import annotations

from functools import lru_cache
import unicodedata


SEMANTIC_SYMBOLS = set("%％$＄€£¥＋+−-=/\\")


def content_chars(text: str) -> str:
    """Return comparable content while preserving numeric/symbol meaning."""
    out = []
    for ch in text:
        cat0 = unicodedata.category(ch)[0]
        if ch in SEMANTIC_SYMBOLS:
            out.append(ch)
        elif cat0 not in ("P", "Z", "S", "C"):
            out.append(ch)
    return "".join(out)


def reachable_by_pairs(source: str, candidate: str, pairs: list[tuple[str, str]] | None = None) -> bool:
    """Whether candidate content is source content plus only authorized replacements."""
    a = content_chars(source)
    b = content_chars(candidate)
    if a == b:
        return True
    normalized_pairs = []
    for wrong, right in pairs or []:
        w = content_chars(str(wrong))
        r = content_chars(str(right))
        if w and r and w != r:
            normalized_pairs.append((w, r))

    @lru_cache(maxsize=None)
    def ok(i: int, j: int) -> bool:
        if i == len(a) and j == len(b):
            return True
        if i < len(a) and j < len(b) and a[i] == b[j] and ok(i + 1, j + 1):
            return True
        for wrong, right in normalized_pairs:
            if a.startswith(wrong, i) and b.startswith(right, j) and ok(i + len(wrong), j + len(right)):
                return True
        return False

    return ok(0, 0)


def assert_no_rewrite(source: str, candidate: str, pairs: list[tuple[str, str]] | None = None) -> None:
    """Raise ValueError if candidate changes content beyond authorized pairs."""
    if not reachable_by_pairs(source, candidate, pairs):
        raise ValueError("candidate rewrites content outside authorized glossary pairs")
