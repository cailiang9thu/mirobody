"""The one test that ships: the resolver's coverage score.

`test_engine_coverage.py` is EVIDENCE for a number the README prints and links,
so it stays in the repository. A benchmark nobody can run is an assertion.

Everything else moved to a gitignored `tests/` at the repo root. Internal
regression tests are not public surface, and this project's readers file issues
rather than patches.

Nothing here reaches the wheel: `scripts/build_backend.py` drops the directory
and `scripts/check_wheel_data.py` fails the build if a test module comes back.
"""
