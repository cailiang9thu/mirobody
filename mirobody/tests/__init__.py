"""The gate suite: one test module per public claim.

These are the tests a CLONE runs (`pytest mirobody`). Each is EVIDENCE for
something the README, SECURITY.md or the docs assert — a benchmark nobody can
run is an assertion, so they ship in the repository. The exhaustive local
regression suite lives in a gitignored `tests/` at the repo root instead.

A package, not a loose directory, because these modules cite each other:
`test_readme_numbers` re-derives the published coverage score from
`test_engine_coverage`'s case table rather than keeping a second copy. Nothing
here reaches the wheel — `scripts/build_backend.py` drops the whole directory
and `scripts/check_wheel_data.py` fails the build if one comes back.
"""

#: The README editions that are LIVE, and the locale each one is written in
#: (`None` = English). 繁體中文 and 日本語 are frozen at 1.4.0 under `archived/`
#: — each drew under 9 unique visitors in the 14 days before the freeze against
#: 263 for zh-CN — and are not gated; `archived/README.md` says how to revive
#: one.
#:
#: ONE place, because four gate modules read it and reviving an edition used to
#: mean finding all four — and finding three of them left the fourth checking a
#: set of editions that no longer matched. The locale is here rather than in
#: `test_readme_l10n` for the same reason.
LIVE_READMES: dict[str, str | None] = {
    "README.md": None,
    "README.zh-CN.md": "zh-CN",
}
