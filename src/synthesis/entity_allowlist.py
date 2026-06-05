"""Curated lowercase tool/runtime allowlist for query-entity extraction.

`extract_query_entities()` in `quality_gate.py` matches four entity shapes
(capitalized words, internal-cap identifiers, hyphenated identifiers, dotted
module paths). Single-word lowercase tools like ``bun`` / ``npm`` / ``deno``
look like ordinary English words and are NOT caught by any of those shapes.
That gap is closed here.

Two tiers:

- ``LOWERCASE_TOOL_ALLOWLIST`` — always safe. Names that are unambiguous
  in any context (the bare word `npm` in a query is overwhelmingly the
  package manager, never a noun in normal prose).

- ``CONTEXTUAL_LOWERCASE_TOOL_ALLOWLIST`` — names that collide with
  ordinary English. ``go`` is a verb, ``rust`` is a metal, ``tar`` is
  road material, ``uv`` is ultraviolet. These only count as entities when
  the surrounding query carries technical/comparison cues (``compare``,
  ``vs``, ``install``, ``runtime``, etc.) per ``CONTEXT_CUES`` below.

A third set, ``LOWERCASE_HYPHENATED_TOOL_ALLOWLIST``, is the Shape 3
escape hatch. Hyphenated identifiers are kept only when they carry an
uppercase letter or a digit (``gpt-4o`` / ``claude-3-5``) OR are listed
here (``scikit-learn`` / ``llama-cpp``). It exists so all-lowercase
hyphenated package names survive the cap-or-digit filter that drops
generic English compounds (``opt-out`` / ``real-time`` / ``pre-recorded``).

All sets are frozenset for O(1) membership and to prevent mutation.

Maintainer-owned, code-reviewed, no env/JSON/config override on purpose:
verifier behavior must be reproducible across deployments. If an operator
needs a one-off entity, they pass ``entities=[...]`` to the call site.
"""

from __future__ import annotations


LOWERCASE_TOOL_ALLOWLIST: frozenset[str] = frozenset({
    # JavaScript / TypeScript ecosystem
    "bun", "deno", "npm", "pnpm", "yarn",
    "jest", "vitest", "mocha", "chai",
    "eslint", "prettier",
    "webpack", "vite", "rollup", "esbuild", "turbo", "nx", "lerna",
    # Python ecosystem
    "pip", "poetry", "conda", "mamba", "hatch",
    "ruff", "mypy", "black", "isort", "pytest",
    # Rust / native build chains
    "cargo", "rustc",
    # JVM ecosystem
    "mvn", "gradle", "sbt",
    # .NET
    "dotnet",
    # Native compilers / build (cmake/perl/ruby/etc. — single-word, unambiguous in tech context)
    "cmake", "perl", "ruby", "java", "php", "lua",
    # Editors / multiplexers
    "vim", "neovim", "tmux",
    # VCS / CLI utilities
    "git", "jq", "yq", "grep", "sed", "awk", "curl", "wget", "ssh",
    # Container / orchestration
    "docker", "kubectl", "helm",
    "terraform", "ansible", "vagrant", "podman",
    # Languages with unambiguous lowercase identifiers
    "zig", "ocaml", "scala", "kotlin", "dart", "julia",
    "opam",
})


CONTEXTUAL_LOWERCASE_TOOL_ALLOWLIST: frozenset[str] = frozenset({
    # Tools whose bare lowercase form collides with ordinary English.
    # Detection requires CONTEXT_CUES to be present in the same query.
    "uv",      # Python installer / ultraviolet
    "go",      # Go language / verb
    "rust",    # Rust language / metal
    "tar",     # Archive / road material
    "make",    # GNU make / verb
    "mix",     # Elixir build / verb
    "gem",     # RubyGems / jewel
    "swift",   # Swift language / adjective
    "crystal", # Crystal language / mineral
})


CONTEXT_CUES: frozenset[str] = frozenset({
    "compare", "comparing", "comparison",
    "vs", "versus",
    "alternative", "alternatives",
    "migrate", "migrating", "migration",
    "install", "installing", "installation",
    "package", "packages",
    "runtime", "runtimes",
    "tool", "tools", "toolchain",
    "cli",
    "benchmark", "benchmarks", "benchmarking",
})


# Shape 3 escape hatch: legitimate all-lowercase hyphenated package/library
# names that carry no uppercase letter or digit, so the cap-or-digit filter
# (which drops generic English compounds like opt-out / real-time) would
# otherwise discard them. Matched case-insensitively against candidate.lower().
LOWERCASE_HYPHENATED_TOOL_ALLOWLIST: frozenset[str] = frozenset({
    "scikit-learn",
    "llama-cpp",
    "llama-cpp-python",
    "react-dom",
    "react-native",
    "styled-components",
    "create-react-app",
    "pip-tools",
    "npm-run-all",
    "node-fetch",
    "ts-node",
    "next-auth",
    "date-fns",
    "huggingface-hub",
    "sentence-transformers",
})


# Descriptive acronym prefixes for hyphenated compounds. A Shape 3 hyphenated
# candidate is treated as a descriptive query MODIFIER (not a named entity) only
# when its FIRST segment is one of these AND every later segment is a curated
# participle in DESCRIPTIVE_TAILS below — `_is_hyphenated_entity` then drops it
# ("AI-served", "AI-Served", "ML-based", "LLM-driven", "GPU-accelerated",
# "API-first") so the coverage gate stops hard-failing it the way it hard-failed
# "Optimal" (ISS-20260606-001). The prefix set alone is NECESSARY-NOT-SUFFICIENT:
# a prefix match whose tail is a ProperNoun / ecosystem name is PRESERVED
# ("AI-Horde", "AR-Foundation", "gRPC-Web"), as are an all-caps acronym tail
# ("AI-SDK", "AI-API"), a digit tail ("AI-2027"), and a non-acronym prefix
# ("Web-LLM"). See DESCRIPTIVE_TAILS (codex 019e721b T6 M2 — the earlier
# "any non-all-caps tail" heuristic wrongly dropped those real identifiers).
DESCRIPTIVE_ACRONYM_PREFIXES: frozenset[str] = frozenset({
    "ai", "ml", "llm", "genai", "nlp",
    "gpu", "cpu", "tpu", "npu",
    "api", "sdk", "cli", "ui", "ux",
    "ci", "cd", "ar", "vr", "xr", "iot",
    "saas", "paas", "iaas",
    "rest", "grpc",
})


# Descriptive participle/adjective tails for acronym-prefixed compounds. A
# Shape 3 hyphenated candidate is descriptive only when its first segment is a
# DESCRIPTIVE_ACRONYM_PREFIX AND every later segment is one of these
# ("AI-served", "ML-based", "GPU-accelerated", "API-first"). Gating on a CURATED
# tail set — rather than "any non-all-caps alpha tail" — preserves real
# hyphenated identifiers whose tail is a ProperNoun or ecosystem name:
# "AR-Foundation" (Unity), "AI-Horde", "gRPC-Web", "AI-SDK" all survive
# (codex 019e721b T6 M2 — the "any tail" heuristic wrongly dropped them).
DESCRIPTIVE_TAILS: frozenset[str] = frozenset({
    "served", "based", "driven", "powered", "enabled", "focused",
    "first", "native", "ready", "oriented", "centric", "aware",
    "friendly", "agnostic", "specific", "related", "backed", "only",
    "free", "augmented", "generated", "assisted", "integrated",
    "accelerated", "optimized", "automated", "hosted", "managed",
    "enhanced", "capable", "compatible", "bound", "heavy", "intensive",
})
