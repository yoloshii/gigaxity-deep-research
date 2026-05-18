"""Abbreviation-aware sentence splitting for the post-synthesis verifier.

The naive splitter ``re.compile(r"[.!?]\\s+|\\n+")`` treats every period as a
sentence terminator, which truncates sentences containing common English
abbreviations (``U.S.``, ``e.g.``, ``Mr.``, etc.) mid-token. The verifier
then evaluates gap-framing checks against broken sentence fragments and
hard-fails synthesis output that was actually well-formed.

Strategy: replace the `.` characters in known abbreviations with a private
sentinel (``\\x00``, never appearing in real synthesis text), split on the
remaining terminators, then restore the sentinels back to `.`. The
abbreviation list is the set codex DESIGN session 019e3a66 locked as
load-bearing for v0.3.x synthesis prompts — common English abbreviations
plus a handful of synthesis-specific ones (``et al.``, ``vs.``, ``cf.``,
``viz.``).

The helpers ``protect_abbreviations`` / ``restore_abbreviations`` are also
applied around ``verification.py``'s claim-extractor regex so cited claims
containing abbreviations are not truncated at the abbreviation period.
"""

from __future__ import annotations

import re


# Common English abbreviations whose trailing `.` is NOT a sentence
# terminator. Lowercased; case-insensitive matching via re.IGNORECASE
# preserves original case during sentinel substitution.
_ABBREVIATIONS: frozenset[str] = frozenset({
    # Geographic / political
    "u.s.", "u.k.", "e.u.", "u.n.",
    # Discourse markers
    "e.g.", "i.e.", "etc.", "vs.", "cf.", "viz.", "et al.",
    # Honorifics
    "mr.", "mrs.", "ms.", "dr.", "prof.", "jr.", "sr.",
    # Corporate / legal
    "inc.", "ltd.", "co.", "corp.", "llc",
    # Degrees
    "ph.d.", "m.d.", "b.a.", "m.a.", "b.s.", "m.s.",
    # Reference shorthand
    "no.", "fig.", "eq.", "sec.",
    # Time of day
    "a.m.", "p.m.",
})

# Sentinel: U+0000 NUL. Never appears in real text streams; if it does,
# something far worse is going on and the splitter's behavior is undefined.
_SENTINEL: str = "\x00"

# Union pattern matches any abbreviation case-insensitively at a word
# boundary. Sorted longest-first so multi-segment abbreviations like
# ``ph.d.`` protect both periods before ``d.`` is considered as a prefix.
_ABBREV_PATTERN: re.Pattern[str] = re.compile(
    r"\b(?:"
    + "|".join(re.escape(a) for a in sorted(_ABBREVIATIONS, key=len, reverse=True))
    + r")",
    flags=re.IGNORECASE,
)

# Sentence-boundary regex (terminator + whitespace, or newline run). Same
# semantics as the previous `output_verifier._SENTENCE_SPLIT`, but operates
# on text that has had abbreviations protected first.
_SENTENCE_SPLIT_PATTERN: re.Pattern[str] = re.compile(r"[.!?]\s+|\n+")


def protect_abbreviations(text: str) -> str:
    """Replace `.` inside known abbreviations with the private sentinel.

    Case-insensitive — the original casing of the abbreviation is preserved
    (only the `.` characters are substituted). Idempotent at the character
    level: applying this twice has no further effect.
    """
    return _ABBREV_PATTERN.sub(
        lambda m: m.group(0).replace(".", _SENTINEL), text
    )


def restore_abbreviations(text: str) -> str:
    """Inverse of ``protect_abbreviations`` — sentinels back to `.`."""
    return text.replace(_SENTINEL, ".")


def split_sentences(text: str) -> list[str]:
    """Split `text` into sentence-like segments, treating common English
    abbreviations as single tokens rather than terminators.

    Returns a list of strings with the same total content (modulo the
    consumed terminator + trailing whitespace) as the input. Empty input
    returns an empty list. Abbreviation casing is preserved in the output.
    """
    if not text:
        return []
    protected = protect_abbreviations(text)
    pieces = _SENTENCE_SPLIT_PATTERN.split(protected)
    return [restore_abbreviations(p) for p in pieces]
