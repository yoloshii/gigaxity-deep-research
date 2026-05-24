"""Tests for scripts/audit_synthesis_callers.py (Phase 0 AST CI gate).

The audit script enforces the architectural invariant that the three core
synthesis classes (SynthesisEngine, SynthesisAggregator,
OutlineGuidedSynthesizer) may be imported only in allowlisted modules. These
tests verify:

- Positive control: a forbidden import in a non-allowlisted file is flagged.
- Negative control: importing legitimate (non-core) types from the same
  modules is NOT flagged.
- Allowlist: files at known-allowlisted paths are NEVER flagged.
- Multiline parenthesized imports — the codex Turn 5 regression — are caught.
- Dynamic-lookup escape hatches (getattr / globals / eval) are caught.
- The tests/ tree is exempt by design (audit walks src/ only).
"""

import importlib.util
import pathlib
import sys
import textwrap
from typing import Callable

import pytest


# ---------------------------------------------------------------------------
# Import the audit module under test directly from its path (it lives in
# scripts/, not src/, so it isn't on the package import path by default).
# ---------------------------------------------------------------------------


_AUDIT_PATH = pathlib.Path(__file__).parent.parent / "scripts" / "audit_synthesis_callers.py"


def _load_audit_module():
    spec = importlib.util.spec_from_file_location("_audit_under_test", _AUDIT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def audit_mod():
    return _load_audit_module()


# ---------------------------------------------------------------------------
# Test fixture builder: a temporary repo root containing a `src/` directory
# with caller files supplied by the test.
# ---------------------------------------------------------------------------


def _make_repo(tmp_path: pathlib.Path, files: dict[str, str]) -> pathlib.Path:
    """Create a fake repo rooted at tmp_path/repo with the given src/ files.

    `files` keys are relative paths from the repo root (e.g. "src/foo.py");
    values are file contents. Files outside `src/` are created at any depth.
    """
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True, exist_ok=True)
    for relpath, content in files.items():
        f = repo / relpath
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    return repo


# ---------------------------------------------------------------------------
# Positive controls — these MUST flag.
# ---------------------------------------------------------------------------


class TestPositiveControls:
    """Files that import a core class outside the allowlist must be flagged."""

    @pytest.mark.unit
    def test_simple_import_of_synthesis_aggregator_flagged(self, audit_mod, tmp_path):
        repo = _make_repo(tmp_path, {
            "src/some_caller.py": """
                from src.synthesis import SynthesisAggregator

                def use_it():
                    return SynthesisAggregator
            """,
        })
        scanned, violations = audit_mod.audit(repo)
        assert any("SynthesisAggregator" in v for v in violations)
        assert any("src/some_caller.py" in v for v in violations)

    @pytest.mark.unit
    def test_simple_import_of_synthesis_engine_flagged(self, audit_mod, tmp_path):
        repo = _make_repo(tmp_path, {
            "src/some_caller.py": """
                from src.synthesis import SynthesisEngine
            """,
        })
        _, violations = audit_mod.audit(repo)
        assert any("SynthesisEngine" in v for v in violations)

    @pytest.mark.unit
    def test_simple_import_of_outline_synthesizer_flagged(self, audit_mod, tmp_path):
        repo = _make_repo(tmp_path, {
            "src/some_caller.py": """
                from src.synthesis import OutlineGuidedSynthesizer
            """,
        })
        _, violations = audit_mod.audit(repo)
        assert any("OutlineGuidedSynthesizer" in v for v in violations)

    @pytest.mark.unit
    def test_multiline_parenthesized_import_flagged_codex_T5(self, audit_mod, tmp_path):
        """codex Turn 5 regression: a multi-line parenthesized import must
        be caught. AST.ImportFrom flattens the aliases regardless of
        whether the source used parentheses — but the prior regex-based
        approach missed this shape (Turn 5 was the exact bug)."""
        repo = _make_repo(tmp_path, {
            "src/multiline_caller.py": """
                from src.synthesis import (
                    SynthesisStyle,
                    SynthesisAggregator,
                    PreGatheredSource,
                )
            """,
        })
        _, violations = audit_mod.audit(repo)
        # The SynthesisAggregator entry in the multi-line tuple is the
        # forbidden one; SynthesisStyle and PreGatheredSource are fine.
        flag_lines = [v for v in violations if "SynthesisAggregator" in v]
        assert flag_lines, f"Expected SynthesisAggregator flag, got: {violations}"

    @pytest.mark.unit
    def test_aliased_import_flagged(self, audit_mod, tmp_path):
        """`from src.synthesis import SynthesisAggregator as SA` flagged
        too (alias.name is what the audit checks, not alias.asname)."""
        repo = _make_repo(tmp_path, {
            "src/aliased_caller.py": """
                from src.synthesis import SynthesisAggregator as SA
            """,
        })
        _, violations = audit_mod.audit(repo)
        assert any("SynthesisAggregator" in v for v in violations)

    @pytest.mark.unit
    def test_submodule_import_of_core_class_flagged(self, audit_mod, tmp_path):
        """`from src.synthesis.aggregator import SynthesisAggregator` flagged."""
        repo = _make_repo(tmp_path, {
            "src/sub_caller.py": """
                from src.synthesis.aggregator import SynthesisAggregator
            """,
        })
        _, violations = audit_mod.audit(repo)
        assert any("SynthesisAggregator" in v for v in violations)

    @pytest.mark.unit
    def test_bare_module_import_of_core_module_flagged(self, audit_mod, tmp_path):
        """`import src.synthesis.aggregator` flagged (the escape hatch where
        a caller imports the bare module then attribute-accesses
        SynthesisAggregator off it)."""
        repo = _make_repo(tmp_path, {
            "src/bare_caller.py": """
                import src.synthesis.aggregator
            """,
        })
        _, violations = audit_mod.audit(repo)
        assert any("src.synthesis.aggregator" in v for v in violations)

    @pytest.mark.unit
    def test_getattr_dynamic_lookup_flagged(self, audit_mod, tmp_path):
        repo = _make_repo(tmp_path, {
            "src/dynamic_caller.py": """
                import src.synthesis as syn
                cls = getattr(syn, "SynthesisAggregator")
            """,
        })
        _, violations = audit_mod.audit(repo)
        assert any("dynamic lookup" in v for v in violations)

    @pytest.mark.unit
    def test_globals_dynamic_lookup_flagged(self, audit_mod, tmp_path):
        repo = _make_repo(tmp_path, {
            "src/globals_caller.py": """
                cls = globals()["SynthesisAggregator"]
            """,
        })
        _, violations = audit_mod.audit(repo)
        assert any("dynamic lookup" in v for v in violations)

    @pytest.mark.unit
    def test_submodule_name_import_flagged_codex_T1_F2(self, audit_mod, tmp_path):
        """codex Turn 1 F2 (High): `from src.synthesis import aggregator`
        then `aggregator.SynthesisAggregator(...)` previously bypassed the
        gate. Both the submodule-name import AND the attribute access must
        be flagged."""
        repo = _make_repo(tmp_path, {
            "src/sneaky_caller.py": """
                from src.synthesis import aggregator
                cls = aggregator.SynthesisAggregator
            """,
        })
        _, violations = audit_mod.audit(repo)
        # Submodule name import flagged.
        assert any("submodule import of core synthesis module 'aggregator'" in v for v in violations)
        # Attribute access flagged independently (belt-and-suspenders).
        assert any("attribute access '.SynthesisAggregator'" in v for v in violations)

    @pytest.mark.unit
    def test_attribute_access_alone_flagged_codex_T1_F2(self, audit_mod, tmp_path):
        """Even WITHOUT a submodule import, any `.SynthesisAggregator` /
        `.SynthesisEngine` / `.OutlineGuidedSynthesizer` attribute access
        outside the allowlist is flagged. Closes dynamic-import variants
        the regex cannot easily catch."""
        repo = _make_repo(tmp_path, {
            "src/attr_caller.py": """
                import some_module
                cls = some_module.SynthesisEngine
                inst = factory().OutlineGuidedSynthesizer()
            """,
        })
        _, violations = audit_mod.audit(repo)
        assert any("attribute access '.SynthesisEngine'" in v for v in violations)
        assert any("attribute access '.OutlineGuidedSynthesizer'" in v for v in violations)

    @pytest.mark.unit
    def test_dunder_import_flagged_codex_T1_F3(self, audit_mod, tmp_path):
        """codex Turn 1 F3 (Medium): `__import__("src.synthesis.aggregator")`
        was not caught by the original dynamic-lookup patterns. Now is."""
        repo = _make_repo(tmp_path, {
            "src/dunder_caller.py": """
                mod = __import__("src.synthesis.aggregator", fromlist=["SynthesisAggregator"])
            """,
        })
        _, violations = audit_mod.audit(repo)
        assert any("dynamic lookup" in v for v in violations)

    @pytest.mark.unit
    def test_finalization_py_flagged_when_importing_core_class_codex_T1_F1(self, audit_mod, tmp_path):
        """codex Turn 1 F1 (High): finalization.py was wholesale allowlisted,
        so it could now import core classes without tripping. After the fix,
        finalization.py is NOT in ALLOWLIST_PATHS and forbidden imports there
        ARE flagged."""
        repo = _make_repo(tmp_path, {
            "src/synthesis/finalization.py": """
                from src.synthesis import SynthesisAggregator  # forbidden
            """,
        })
        _, violations = audit_mod.audit(repo)
        assert any(
            "src/synthesis/finalization.py" in v and "SynthesisAggregator" in v
            for v in violations
        )

    @pytest.mark.unit
    def test_vars_dict_access_flagged_codex_T2_F1(self, audit_mod, tmp_path):
        """codex Turn 2 F1 (Low): `vars(mod)["SynthesisAggregator"]` bypassed
        the gate (regex doesn't match, ast.Attribute doesn't apply since
        Subscript is the access shape). Now an ast.Subscript walker flags
        any string-Constant slice naming a core class."""
        repo = _make_repo(tmp_path, {
            "src/vars_caller.py": """
                import src.synthesis as syn
                cls = vars(syn)["SynthesisAggregator"]
            """,
        })
        _, violations = audit_mod.audit(repo)
        assert any("subscript access ['SynthesisAggregator']" in v for v in violations)

    @pytest.mark.unit
    def test_dunder_dict_access_flagged_codex_T2_F1(self, audit_mod, tmp_path):
        """Same class as above: `syn.__dict__["SynthesisAggregator"]`."""
        repo = _make_repo(tmp_path, {
            "src/dict_caller.py": """
                import src.synthesis as syn
                cls = syn.__dict__["SynthesisEngine"]
            """,
        })
        _, violations = audit_mod.audit(repo)
        assert any("subscript access ['SynthesisEngine']" in v for v in violations)

    @pytest.mark.unit
    def test_locals_dict_access_flagged_codex_T2_F1(self, audit_mod, tmp_path):
        """`locals()["CoreClass"]` is the local-frame variant of the same
        bypass shape."""
        repo = _make_repo(tmp_path, {
            "src/locals_caller.py": """
                cls = locals()["OutlineGuidedSynthesizer"]
            """,
        })
        _, violations = audit_mod.audit(repo)
        assert any("subscript access ['OutlineGuidedSynthesizer']" in v for v in violations)

    @pytest.mark.unit
    def test_innocent_dict_access_with_unrelated_key_not_flagged(self, audit_mod, tmp_path):
        """Negative control: regular dict access with non-CORE_CLASSES keys
        is NOT flagged."""
        repo = _make_repo(tmp_path, {
            "src/normal_caller.py": """
                config = {"setting": "value"}
                x = config["setting"]
                y = ["a", "b"][0]
            """,
        })
        _, violations = audit_mod.audit(repo)
        assert violations == []


# ---------------------------------------------------------------------------
# Negative controls — these MUST NOT flag.
# ---------------------------------------------------------------------------


class TestNegativeControls:
    """Files that import only legitimate non-core types are NOT flagged."""

    @pytest.mark.unit
    def test_import_synthesis_style_not_flagged(self, audit_mod, tmp_path):
        """SynthesisStyle is a dataclass / enum, NOT a core class."""
        repo = _make_repo(tmp_path, {
            "src/style_caller.py": """
                from src.synthesis import SynthesisStyle
            """,
        })
        _, violations = audit_mod.audit(repo)
        assert violations == []

    @pytest.mark.unit
    def test_import_pre_gathered_source_not_flagged(self, audit_mod, tmp_path):
        """PreGatheredSource is a dataclass, NOT a core class."""
        repo = _make_repo(tmp_path, {
            "src/pgs_caller.py": """
                from src.synthesis import PreGatheredSource, SynthesisStyle
            """,
        })
        _, violations = audit_mod.audit(repo)
        assert violations == []

    @pytest.mark.unit
    def test_submodule_import_of_type_only_not_flagged(self, audit_mod, tmp_path):
        """`from .aggregator import SynthesisStyle` is a type-only sub-module
        import — legitimately used by synthesis/presets.py and rcs.py."""
        repo = _make_repo(tmp_path, {
            "src/synthesis/internal_consumer.py": """
                from src.synthesis.aggregator import SynthesisStyle, PreGatheredSource
            """,
        })
        _, violations = audit_mod.audit(repo)
        # No CORE_CLASSES name in the import — passes.
        assert violations == []

    @pytest.mark.unit
    def test_unrelated_imports_not_flagged(self, audit_mod, tmp_path):
        repo = _make_repo(tmp_path, {
            "src/some_caller.py": """
                import json
                from pathlib import Path
                from typing import Optional
            """,
        })
        _, violations = audit_mod.audit(repo)
        assert violations == []


# ---------------------------------------------------------------------------
# Allowlist — files at allowlisted paths are NEVER flagged regardless of imports.
# ---------------------------------------------------------------------------


class TestAllowlist:
    @pytest.mark.unit
    def test_wrappers_py_allowed_to_import_core_classes(self, audit_mod, tmp_path):
        """src/synthesis/wrappers.py — the ONE module allowed to consume
        the three core classes — must not be flagged regardless of imports."""
        repo = _make_repo(tmp_path, {
            "src/synthesis/wrappers.py": """
                from src.synthesis.aggregator import SynthesisAggregator
                from src.synthesis.engine import SynthesisEngine
                from src.synthesis.outline import OutlineGuidedSynthesizer
            """,
        })
        _, violations = audit_mod.audit(repo)
        assert violations == []

    @pytest.mark.unit
    def test_synthesis_init_allowed_to_import_core_classes(self, audit_mod, tmp_path):
        """src/synthesis/__init__.py re-exports the public surface,
        including the core classes for downstream test imports."""
        repo = _make_repo(tmp_path, {
            "src/synthesis/__init__.py": """
                from src.synthesis.aggregator import SynthesisAggregator
                from src.synthesis.engine import SynthesisEngine
            """,
        })
        _, violations = audit_mod.audit(repo)
        assert violations == []

    @pytest.mark.unit
    def test_core_modules_allowed_to_self_reference(self, audit_mod, tmp_path):
        """aggregator.py / engine.py / outline.py define the classes themselves."""
        repo = _make_repo(tmp_path, {
            "src/synthesis/aggregator.py": "class SynthesisAggregator: ...",
            "src/synthesis/engine.py": "class SynthesisEngine: ...",
            "src/synthesis/outline.py": "class OutlineGuidedSynthesizer: ...",
        })
        _, violations = audit_mod.audit(repo)
        assert violations == []

    @pytest.mark.unit
    def test_finalization_can_import_result_types_without_allowlist(self, audit_mod, tmp_path):
        """After codex T1 F1 finalization.py is NOT allowlisted, but its
        existing imports (AggregatedSynthesis, OutlinedSynthesis,
        SynthesisStyle, etc.) are non-CORE result types — none of them
        appear in CORE_CLASSES, so the gate passes without an allowlist
        entry. The gate now also catches a forbidden import in this file
        (see TestPositiveControls::test_finalization_py_flagged_when_...).
        """
        repo = _make_repo(tmp_path, {
            "src/synthesis/finalization.py": """
                from src.synthesis.aggregator import AggregatedSynthesis
                from src.synthesis.outline import OutlinedSynthesis
                from src.synthesis import SynthesisStyle
            """,
        })
        _, violations = audit_mod.audit(repo)
        assert violations == []


# ---------------------------------------------------------------------------
# Scope — tests/ tree is intentionally NOT audited.
# ---------------------------------------------------------------------------


class TestScope:
    @pytest.mark.unit
    def test_audit_walks_src_only(self, audit_mod, tmp_path):
        """A forbidden import in tests/ is NOT flagged — the audit walks
        src/ only. Tests must be free to import core classes for direct
        verification of class-level behavior."""
        repo = _make_repo(tmp_path, {
            "tests/test_aggregator.py": """
                from src.synthesis import SynthesisAggregator
                # legitimate direct test of the class
            """,
        })
        scanned, violations = audit_mod.audit(repo)
        # `tests/` is NOT walked: scanned reports 0 src files, 0 violations.
        assert violations == []
        assert scanned == 0

    @pytest.mark.unit
    def test_audit_returns_no_src_dir_error_when_src_missing(self, audit_mod, tmp_path):
        """A repo without `src/` returns a single notice violation."""
        repo = tmp_path / "no_src_repo"
        repo.mkdir()
        scanned, violations = audit_mod.audit(repo)
        assert scanned == 0
        assert any("no 'src/' directory" in v for v in violations)


# ---------------------------------------------------------------------------
# Exit-code contract — main() returns 0 on clean, 1 on violation.
# ---------------------------------------------------------------------------


class TestExitCodes:
    @pytest.mark.unit
    def test_main_returns_zero_on_clean_repo(self, audit_mod, tmp_path, capsys):
        repo = _make_repo(tmp_path, {
            "src/clean.py": "import json",
        })
        exit_code = audit_mod.main(["script", str(repo)])
        assert exit_code == 0

    @pytest.mark.unit
    def test_main_returns_one_on_violation(self, audit_mod, tmp_path, capsys):
        repo = _make_repo(tmp_path, {
            "src/violating.py": "from src.synthesis import SynthesisAggregator",
        })
        exit_code = audit_mod.main(["script", str(repo)])
        assert exit_code == 1
