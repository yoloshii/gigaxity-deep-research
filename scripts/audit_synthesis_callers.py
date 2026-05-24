#!/usr/bin/env python3
"""Phase 0 AST CI gate — enforce wrappers.py monopoly on synthesis core calls.

Architectural invariant (Phase 0 of the gigaxity paper-enhancement plan):
the three core synthesis classes (`SynthesisEngine`, `SynthesisAggregator`,
`OutlineGuidedSynthesizer`) may be imported and instantiated only inside the
allowlisted modules. Every other code path must go through one of the five
wrappers in `src/synthesis/wrappers.py`, which guarantee
`finalize_synthesis` runs on the result.

This script walks `src/` (production code; tests are exempt) and parses every
`.py` file's AST. A non-allowlisted file is flagged if it:
- imports any of the three core class names by `from src.synthesis ... import
  SynthesisAggregator` style or any nested module re-export form;
- imports the bare module that defines a core class (`from src.synthesis
  import aggregator` is ambiguous and unusual; we flag it for review).

A complementary plain-text scan catches the dynamic-lookup escape hatches
(`getattr(mod, "SynthesisAggregator")`, `globals()["SynthesisAggregator"]`,
`eval("SynthesisAggregator")`) so a caller cannot route around the AST gate
by stringifying the class name.

Run from the repo root:

    python scripts/audit_synthesis_callers.py

Exit codes:
- 0: no violations
- 1: at least one forbidden import/lookup in non-allowlisted code

Designed to be stdlib-only so it can run pre-commit / pre-merge with no
project venv required.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

# Repo-rooted import paths the audit treats as forbidden when they appear in
# non-allowlisted source. The three classes are gigaxity's synthesis cores —
# entry points to the LLM-driven generation step. Phase 0 invariant: every
# call to one of these classes' methods must originate from `wrappers.py`.
CORE_CLASSES = frozenset({
    "SynthesisEngine",
    "SynthesisAggregator",
    "OutlineGuidedSynthesizer",
})

# Core submodule names. `from src.synthesis import aggregator` then
# `aggregator.SynthesisAggregator(...)` bypassed the CORE_CLASSES check
# (codex Turn 1 F2). These names are flagged regardless of whether the
# source module is the synthesis package — they are highly synthesis-specific
# and the false-positive risk is minimal.
CORE_PACKAGE_MODULES = frozenset({
    "aggregator",
    "engine",
    "outline",
})

# Paths (relative to repo root) where importing a core class is legitimate.
# - `aggregator.py`, `engine.py`, `outline.py`: the class definitions themselves
# - `__init__.py`: re-exports the public synthesis surface
# - `wrappers.py`: the ONE module allowed to call core methods on instances
#
# finalization.py is NOT allowlisted (codex Turn 1 F1) — the gate must still
# enforce its "pure post-result, no core-method calls" contract. Its existing
# imports (AggregatedSynthesis, OutlinedSynthesis, SynthesisStyle) are
# non-CORE result types, so they pass the gate without an allowlist entry.
ALLOWLIST_PATHS = frozenset({
    "src/synthesis/aggregator.py",
    "src/synthesis/engine.py",
    "src/synthesis/outline.py",
    "src/synthesis/__init__.py",
    "src/synthesis/wrappers.py",
})

# Bare module names the three classes live in. A `from src.synthesis.aggregator
# import ...` or `from src.synthesis.engine import ...` (any symbol) tightly
# couples the caller to a core module's internals and is also forbidden.
CORE_MODULES = frozenset({
    "src.synthesis.aggregator",
    "src.synthesis.engine",
    "src.synthesis.outline",
    "synthesis.aggregator",   # `from synthesis.aggregator` (intra-package)
    "synthesis.engine",
    "synthesis.outline",
    ".aggregator",            # `from .aggregator import ...` (intra-pkg relative)
    ".engine",
    ".outline",
    "..synthesis.aggregator", # `from ..synthesis.aggregator` (sibling-pkg)
    "..synthesis.engine",
    "..synthesis.outline",
})

# Plain-text patterns that catch the dynamic-lookup escape hatches the AST
# walker cannot see by design (string-named class lookups are runtime values,
# not import statements). `__import__("...")` added codex Turn 1 F3.
DYNAMIC_LOOKUP_PATTERNS = [
    re.compile(r'getattr\s*\([^,]+,\s*[\'"](' + "|".join(CORE_CLASSES) + r')[\'"]'),
    re.compile(r'globals\s*\(\s*\)\s*\[\s*[\'"](' + "|".join(CORE_CLASSES) + r')[\'"]\s*\]'),
    re.compile(r'eval\s*\(\s*[\'"](' + "|".join(CORE_CLASSES) + r')[\'"]'),
    re.compile(r'importlib\.import_module\s*\([^)]*(' + "|".join(CORE_MODULES) + r')'),
    re.compile(r'__import__\s*\(\s*[\'"](' + "|".join(CORE_MODULES) + r')[\'"]'),
]


def _audit_file(path: Path, repo_root: Path) -> list[str]:
    """Return a list of `path:line: detail` violations for one file.

    AST walk catches static imports of CORE_CLASSES (handles multiline
    parenthesized `from x import (a, b, c)` forms which a flat regex misses)
    and `import` of bare CORE_MODULES. Plain-text scan catches dynamic-lookup
    escape hatches.

    Files at allowlisted relative paths are skipped entirely.
    """
    rel = path.relative_to(repo_root).as_posix()
    if rel in ALLOWLIST_PATHS:
        return []

    try:
        source = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []

    violations: list[str] = []

    # AST walk handles the common static-import shapes:
    #   from src.synthesis import SynthesisAggregator           (ImportFrom)
    #   from src.synthesis import (X, SynthesisAggregator, Y)   (ImportFrom)
    #   from src.synthesis.aggregator import anything           (ImportFrom)
    #   import src.synthesis.aggregator                         (Import)
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        # Not a parseable Python file (or version skew); skip — separate lint
        # catches syntax errors.
        return []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # `from src.synthesis import (a, SynthesisAggregator, b)` — flat
            # iteration over aliases catches parenthesized-multiline imports
            # because Python's AST flattens them into one ImportFrom node with
            # multiple aliases (codex DESIGN T5 regression: a flat regex would
            # have missed the multiline form).
            #
            # NOT flagged here: bare-module imports of `from .aggregator
            # import SynthesisStyle` (or any other type). `SynthesisStyle`,
            # `PreGatheredSource`, and `AggregatedSynthesis` are dataclasses /
            # enums, not method-bearing core classes — the Phase 0 invariant
            # is about METHOD CALLS, not module boundaries. The CORE_CLASSES
            # check below already catches `from x.aggregator import
            # SynthesisAggregator` (the alias name matches), which is the
            # actual attack vector.
            for alias in node.names:
                if alias.name in CORE_CLASSES:
                    violations.append(
                        f"{rel}:{node.lineno}: forbidden import of core class "
                        f"'{alias.name}' (move call through src/synthesis/wrappers.py)"
                    )
                # codex Turn 1 F2: `from src.synthesis import aggregator` then
                # `aggregator.SynthesisAggregator(...)` was a bypass — flag
                # submodule imports of the three class-bearing modules.
                elif alias.name in CORE_PACKAGE_MODULES:
                    violations.append(
                        f"{rel}:{node.lineno}: forbidden submodule import of "
                        f"core synthesis module '{alias.name}' (move call "
                        f"through src/synthesis/wrappers.py)"
                    )
        elif isinstance(node, ast.Import):
            # `import src.synthesis.aggregator` then `src.synthesis.aggregator
            # .SynthesisAggregator(...)` would bypass the CORE_CLASSES check
            # if we only audited ImportFrom. Flag bare-module imports of the
            # three class-bearing modules so this attack vector is closed.
            for alias in node.names:
                if alias.name in CORE_MODULES:
                    violations.append(
                        f"{rel}:{node.lineno}: forbidden bare-module import of "
                        f"'{alias.name}' (move through src/synthesis/wrappers.py)"
                    )
        elif isinstance(node, ast.Attribute):
            # codex Turn 1 F2: attribute access on an imported submodule
            # (`aggregator.SynthesisAggregator(...)`) bypassed the gate. This
            # catches any `<expr>.SynthesisAggregator|SynthesisEngine|
            # OutlineGuidedSynthesizer` reference in non-allowlisted code,
            # regardless of how the receiver was obtained (static import,
            # dynamic import, getattr chain, etc.). The attribute check is the
            # belt to the static-import suspenders.
            if node.attr in CORE_CLASSES:
                violations.append(
                    f"{rel}:{node.lineno}: forbidden attribute access "
                    f"'.{node.attr}' on a core synthesis class (move call "
                    f"through src/synthesis/wrappers.py)"
                )
        elif isinstance(node, ast.Subscript):
            # codex Turn 2 F1: dict-access bypass via `vars(syn)["CoreClass"]`
            # or `syn.__dict__["CoreClass"]` was uncovered by the
            # static-import + ast.Attribute pair. Flag any Subscript with a
            # string-Constant slice naming a core class. Catches:
            #   vars(syn)["SynthesisAggregator"]
            #   syn.__dict__["SynthesisAggregator"]
            #   locals()["SynthesisAggregator"]
            #   any_mapping["SynthesisAggregator"]
            # The string literal is highly synthesis-specific so the
            # false-positive risk on innocent dict access is minimal.
            slice_node = node.slice
            if isinstance(slice_node, ast.Constant) and slice_node.value in CORE_CLASSES:
                violations.append(
                    f"{rel}:{node.lineno}: forbidden subscript access "
                    f"['{slice_node.value}'] for a core synthesis class "
                    f"(move call through src/synthesis/wrappers.py)"
                )

    # Plain-text dynamic-lookup scan. Run line-by-line so we can report the
    # exact line number of the violation alongside its match.
    for lineno, line in enumerate(source.splitlines(), start=1):
        for pattern in DYNAMIC_LOOKUP_PATTERNS:
            if pattern.search(line):
                violations.append(
                    f"{rel}:{lineno}: dynamic lookup of core class — "
                    f"line: {line.strip()[:120]}"
                )
                break  # one report per line is plenty

    return violations


def audit(repo_root: Path) -> tuple[int, list[str]]:
    """Walk repo's `src/` tree and collect violations.

    Returns (num_files_scanned, violations_list). Tests are intentionally
    NOT audited — tests import the classes to verify them directly.
    """
    src_root = repo_root / "src"
    if not src_root.is_dir():
        return 0, [f"{repo_root}: no 'src/' directory found"]

    all_violations: list[str] = []
    scanned = 0
    for path in sorted(src_root.rglob("*.py")):
        if path.is_file():
            scanned += 1
            all_violations.extend(_audit_file(path, repo_root))
    return scanned, all_violations


def main(argv: list[str]) -> int:
    repo_root = Path(argv[1] if len(argv) > 1 else ".").resolve()
    scanned, violations = audit(repo_root)
    if violations:
        print(
            f"audit_synthesis_callers: FAIL — {len(violations)} violation(s) "
            f"in {scanned} source file(s) under {repo_root / 'src'}",
            file=sys.stderr,
        )
        for v in violations:
            print(v, file=sys.stderr)
        return 1
    print(
        f"audit_synthesis_callers: OK — {scanned} source file(s) scanned, "
        f"zero forbidden imports outside the wrappers.py allowlist."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
