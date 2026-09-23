"""The ConText engine (`assertion/context.py`) over the cue files (`res/cues/`), 2026-09-23.

Cue words are data: the English trigger set is medspaCy's, vendored verbatim; the Chinese one is
what rules.py used to hard-code. These tests pin the mechanism (scope, terminators, pseudo
triggers, target overlap) and the two provenance facts (the vendored file is the published one;
rules.py carries no cue words)."""
import hashlib
import re
from pathlib import Path

from mirobody_rare.assertion import extract
from mirobody_rare.assertion.context import CUES, lexicon

RULES_PY = Path(__file__).resolve().parent.parent / "mirobody_rare" / "assertion" / "rules.py"


def by_core(text):
    return {a.core.lower(): a for a in extract(text)}


def test_the_vendored_medspacy_rules_are_the_published_file():
    # medspacy/medspacy@b7bb7d8 resources/en/context_rules.json (MIT); a local edit belongs in en.yaml
    got = hashlib.sha256((CUES / "en.medspacy.json").read_bytes()).hexdigest()
    assert got == "5a0096af3761232288475a2b1f8cfbcc1aa2b3eb50c92b8bd12f8032cafec7c2"
    assert len([r for r in lexicon("en").rules if r.source == "en.medspacy.json"]) == 102


def test_rules_py_holds_no_cue_words():
    src = RULES_PY.read_text(encoding="utf-8")
    code = re.sub(r'"""[\s\S]*?"""', "", src)                 # docstrings may quote examples
    code = "\n".join(l.split("#", 1)[0] for l in code.splitlines())
    assert not re.search(r"[一-鿿]", code), "a Chinese cue is back in rules.py"
    for w in ("denies", "without", "mother", "brother", "negative", "history"):
        assert f'"{w}' not in code.lower() and f"'{w}" not in code.lower(), w


def test_forward_negation_reaches_every_finding_in_its_scope():
    got = by_core("He denies gait disturbance and walks independently without ataxia")
    assert got["gait disturbance"].polarity == "absent" and got["ataxia"].polarity == "absent"
    assert [(a.polarity, a.core) for a in extract("No hearing loss, seizures or ataxia.")] == \
        [("absent", "hearing loss"), ("absent", "seizures"), ("absent", "ataxia")]


def test_a_terminator_ends_the_negation_scope():
    got = by_core("He denies diplopia but has ataxia")
    assert (got["diplopia"].polarity, got["ataxia"].polarity) == ("absent", "present")


def test_post_negation_reaches_back_to_a_finding_named_first():
    assert by_core("A strawberry tongue was not observed at any point")["strawberry tongue"].polarity == "absent"
    assert by_core("Ataxia was not observed")["ataxia"].polarity == "absent"


def test_a_trigger_inside_a_finding_name_is_not_a_trigger():
    """"decline" is a medspaCy negation trigger ("patient declined"); inside "Cognitive decline" it
    names the finding, and must not negate the dementia after it."""
    got = by_core("Cognitive decline has progressed to dementia")
    assert got["dementia"].polarity == "present"


def test_a_trigger_that_shares_a_word_with_the_finding_still_negates_it():
    [a] = [a for a in extract("No abnormal pyramidal sign was observed.") if "pyramidal" in a.core.lower()]
    assert a.polarity == "absent"


def test_a_relative_is_the_experiencer_but_not_when_only_the_informant():
    assert by_core("Her mother had seizures")["seizures"].subject_role == "mother"
    assert by_core("Both of his brothers have scoliosis")["scoliosis"].subject_role == "sibling"
    assert by_core("There is a family history of epilepsy")["epilepsy"].subject == "relative"
    assert by_core("His wife has observed chorea involving the face")["chorea"].subject == "proband"
    assert by_core("His father was reported to have had seizures")["seizures"].subject_role == "father"


def test_uncertainty_wins_over_presence():
    assert by_core("Possible ataxia on examination")["ataxia"].polarity == "uncertain"


def test_a_sentence_with_no_known_term_still_yields_one_assertion_for_review():
    [a] = extract("He enjoys fishing with friends on weekends.")
    assert a.core == "He enjoys fishing with friends on weekends"


def test_chinese_stays_in_anchored_mode():
    assert lexicon("zh").mode == "anchored" and lexicon("en").mode == "scope"
    [a] = extract("患者无明显诱因出现头痛")          # "无" mid-clause is not a clause-start negation
    assert a.polarity == "present"
