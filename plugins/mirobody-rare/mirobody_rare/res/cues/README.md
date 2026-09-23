# Assertion cues

Data consumed by `mirobody_rare/assertion/context.py` (ConText, Harkema et al. 2009). No cue word
lives in Python; to change what the extractor reads as a negation / uncertainty / relative, edit
these files.

| file | what | source |
|---|---|---|
| `en.medspacy.json` | English ConText rules (102), **verbatim** | [medspacy/medspacy](https://github.com/medspacy/medspacy) `resources/en/context_rules.json` @ `b7bb7d82f2e8fbadc9ab26d6b4c37578f8883c2c`, MIT (`LICENSE.medspacy`); sha256 pinned in `tests/test_context_engine.py` |
| `en.yaml` | English mode + what the medspaCy set lacks | local; experiencer terminators from Chapman's ConText experiencer list ([chapmanbe/negex](https://github.com/chapmanbe/negex) `genConText/experiencer_triggers.txt`, `[CONJ]`) |
| `zh.yaml` | Chinese cues | local; moved verbatim out of `assertion/rules.py` on 2026-09-23 |

## Rules for changing a file

* **Do not edit `en.medspacy.json`.** Re-vendor a newer upstream commit (and update the pin), or
  put the local difference in `en.yaml`.
* Every local entry says **why** (the held-out sentence that motivated it) and **what it costs**.
  Motivating sentences come from a held-out set's *dev* half (e.g. `rare_coding-p5-en-llm`,
  cases < 60), never from the deterministic benchmark templates and never from a test half.
* A change is reported with two numbers: the benchmark package and the held-out set.

## Modes

* `scope` (English): targets are the HPO terms found in the sentence (`HpoAdapter.find_all`); a
  trigger modifies the targets in its scope — FORWARD to the next `TERMINATE` of its category,
  BACKWARD to the previous one. `PSEUDO` matches only block shorter overlapping triggers. A trigger
  wholly inside a target is dropped (medspaCy's `prune_on_target_overlap`, narrowed to containment).
* `anchored` (Chinese): a trigger counts only at its `anchor` (clause start / clause end / anywhere).
  Chinese stays here until there is a held-out Chinese set to measure a switch to `scope` on.
