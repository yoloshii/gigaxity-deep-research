"""Extract the delimited synthesis answer from a model completion.

The free-form synthesis prompts (comprehensive / concise / comparative /
academic in ``aggregator.py``, and ``RESEARCH_SYSTEM_PROMPT`` in
``prompts.py``) instruct the model to wrap its synthesis in ``<answer>`` /
``</answer>`` tags and put nothing after the closing tag. This module extracts
that delimited content.

Why a delimiter instead of a heuristic stripper: a verbose reasoning model
sometimes appends a trailing "changelog" narrating its own edits
("Removed all false claims ...", "Cited all claims to sources ..."). A
post-hoc heuristic that tried to recognise and delete that block could not be
made safe — five rounds of adversarial review (codex GPT-5.5, 2026-05-29)
proved the model's self-changelog is lexically indistinguishable from a
legitimate topical section in an errata / legal / correction-notice synthesis
(both are verb-first bullets about "claims"/"sources" under a bold heading).
Any rule precise enough to never delete real errata content also failed to
catch the real leak.

Delimiter extraction sidesteps the ambiguity entirely: it acts ONLY on the
sentinel boundary, never on content semantics. The model's changelog lands
AFTER ``</answer>`` and is dropped; everything inside ``<answer>`` — including
a legitimate ``## Corrections Made`` section, if the topic warrants one — is
preserved verbatim. The reasoning path (``synthesize_with_reasoning``) is
already immune by the same mechanism via its ``<synthesis>`` tags.

Fallback is non-destructive: if the tags are absent (the model ignored the
format instruction), the full text is returned UNCHANGED. A missing-delimiter
completion is a cosmetic risk (a changelog might show), never data loss. The
prompt-level format instruction is the primary mechanism; this extractor is
the deterministic enforcement of it.
"""

from __future__ import annotations

import re

ANSWER_OPEN = "<answer>"
ANSWER_CLOSE = "</answer>"

# Content between the first <answer> and its matching </answer> (case-
# insensitive, spanning newlines). Non-greedy so a stray later </answer>
# doesn't swallow trailing junk into the answer.
_ANSWER_BLOCK: re.Pattern[str] = re.compile(
    r"<answer>\s*(.*?)\s*</answer>", re.DOTALL | re.IGNORECASE
)
_OPEN_COUNT: re.Pattern[str] = re.compile(r"<answer>", re.IGNORECASE)
_CLOSE_COUNT: re.Pattern[str] = re.compile(r"</answer>", re.IGNORECASE)

# A tiny chatbot preamble that may precede the wrap ("Here is the synthesis:").
# Start-anchored to a closed set AND must end with a colon, so it never matches
# real answer prose that merely begins with "This is ...".
_TRIVIAL_PREAMBLE: re.Pattern[str] = re.compile(
    r"^(?:sure[,!.]?\s*)?(?:here(?:'s| is| are)|below is|the following is)\b.{0,60}:\s*$",
    re.IGNORECASE | re.DOTALL,
)


def _is_trivial_preamble(before: str) -> bool:
    return len(before) <= 80 and _TRIVIAL_PREAMBLE.match(before) is not None


def extract_delimited_answer(text: str) -> str:
    """Return the synthesis wrapped in ``<answer>…</answer>``, dropping anything
    after the closing tag (e.g. an appended self-edit changelog).

    Extraction fires ONLY on an unambiguous single wrap — exactly one ``<answer>``
    and one ``</answer>``. Everything else is handled non-destructively (content
    is never truncated or dropped on a heuristic basis):

    - A START-ANCHORED single wrap (exactly one open + one close, with the open
      tag at the start modulo a tiny "Here is:" preamble) → the inner content,
      dropping the post-close region (the appended changelog). This is the only
      case that modifies the text.
    - Anything else — no tags; ambiguous, unbalanced, or nested counts; a literal
      ``<answer>`` pair used as content; or a (1,1) pair with substantive text
      before it (partial wrap) → returned UNCHANGED. Never extract a non-wrapper,
      strip literal delimiter syntax, or truncate.
    - Empty / whitespace input → unchanged.
    """
    if not text or not text.strip():
        return text

    n_open = len(_OPEN_COUNT.findall(text))
    n_close = len(_CLOSE_COUNT.findall(text))

    if n_open == 1 and n_close == 1:
        match = _ANSWER_BLOCK.search(text)
        if match:
            inner = match.group(1).strip()
            before = text[:match.start()].strip()
            # Extract ONLY a START-ANCHORED wrapper: open tag at the start
            # (modulo a tiny "Here is:" preamble), so the answer is wrapped from
            # the beginning and the post-close region is the changelog to drop.
            # Substantive content BEFORE the open tag means a (1,1) count that is
            # NOT a whole-answer wrapper — a partial wrap, or a literal <answer>
            # pair used as content (a synthesis ABOUT delimiters). Return
            # unchanged: never extract a non-wrapper, never strip, never truncate.
            if inner and (not before or _is_trivial_preamble(before)):
                return inner

    # No tags, OR any ambiguous count (unbalanced / nested / a literal <answer>
    # inside a synthesis ABOUT delimiters / truncated close) → return the text
    # UNCHANGED. Stripping tags here would delete literal delimiter syntax that
    # is itself substantive content, so on ambiguity we modify nothing.
    return text
