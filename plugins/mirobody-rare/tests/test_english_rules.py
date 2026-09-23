"""English clinical text (haenv batch rare_coding-p4-en, 2026-09-23): the rules were Chinese-only,
so every English negation was coded as present (43/45 wrong, 2 missed), every relative's finding
was filed under the patient (13/13), and "cut the right index finger" coded HP:0012834 *Right*."""
from mirobody_rare.assertion import extract
from mirobody_rare.coding import code_text
from mirobody_rare.text_hook import assertions_of


def one(text):
    [a] = extract(text)
    return a


def test_english_negations_are_absent_and_the_cue_is_stripped():
    for s, core in [("No diplopia was observed.", "diplopia"),
                    ("Examination showed no tall stature.", "tall stature"),
                    ("The patient denies myalgia.", "myalgia"),
                    ("Negative for seizure.", "seizure"),
                    ("No subcutaneous nodule was found.", "subcutaneous nodule"),
                    ("Denies glossitis", "glossitis"),
                    ("No gait disturbance on examination", "gait disturbance"),
                    ("Without ataxia.", "ataxia")]:
        a = one(s)
        assert (a.polarity, a.core) == ("absent", core), (s, a.polarity, a.core)


def test_packed_english_negation_expands_to_one_absent_per_item():
    got = [(a.polarity, a.core) for a in extract("No hearing loss, seizures or ataxia.")]
    assert got == [("absent", "hearing loss"), ("absent", "seizures"), ("absent", "ataxia")]


def test_english_present_fillers_are_stripped_and_polarity_stays_present():
    for s, core in [("At onset, the patient developed seizure.", "seizure"),
                    ("Glioblastoma multiforme gradually became apparent.", "Glioblastoma multiforme"),
                    ("Subsequently, breast carcinoma became noticeable.", "breast carcinoma"),
                    ("Later, hypotonia was also present.", "hypotonia"),
                    ("Recent onset of ataxia", "ataxia"),
                    ("Presents with scoliosis", "scoliosis")]:
        cores = [(a.polarity, a.core) for a in extract(s) if a.core.lower() not in ("at onset", "subsequently", "later")]
        assert cores == [("present", core)], (s, cores)


def test_english_relatives_are_relatives_and_their_role_is_kept():
    for s, role, core in [("The patient's mother has a history of breast carcinoma.", "mother", "breast carcinoma"),
                          ("The patient's father previously had seizure.", "father", "seizure"),
                          ("The patient's sister also experienced ataxia.", "sibling", "ataxia"),
                          ("The patient's maternal uncle also had hypotonia.", "other_relative", "hypotonia"),
                          ("His brother has scoliosis", "sibling", "scoliosis"),
                          ("Family history of epilepsy.", "other_relative", "epilepsy")]:
        a = one(s)
        assert (a.subject, a.subject_role, a.core) == ("relative", role, core), (s, a.subject, a.subject_role, a.core)


def test_the_patient_themself_is_not_a_relative():
    for s in ("The patient reported fatigue.", "The patient's gait was unsteady."):
        assert all(a.subject == "proband" for a in extract(s)), s


def test_a_laterality_word_is_not_a_phenotype():
    """Only terms under Phenotypic abnormality (HP:0000118) code a phenotype assertion: `right`,
    `left`, `bilateral` are clinical modifiers, and everyday text is full of them."""
    res = code_text("While sorting papers the patient cut the right index finger")
    assert "HP:0012834" not in {c.hpo.hpo_id for c in res.coded}
    assert {c.hpo.hpo_id for c in code_text("Seizure").coded} == {"HP:0001250"}


def test_an_english_line_with_several_sentences_splits_into_sentences():
    doc = "## Physical Examination\nNo diplopia was observed. The patient denies myalgia. Vital signs were 36.5 C.\n"
    got = [(a.polarity, a.core, doc[a.char_span[0]:a.char_span[1]]) for _, a in assertions_of(doc) if a.core in ("diplopia", "myalgia")]
    assert got == [("absent", "diplopia", "No diplopia was observed"), ("absent", "myalgia", "The patient denies myalgia")]


def test_chinese_rules_are_untouched():
    a, b, c = one("未见听力受损"), one("母亲也出现过癫痫发作"), one("姐姐有乳腺癌")
    assert (a.polarity, a.core) == ("absent", "听力受损")
    assert (b.subject, b.subject_role, b.core) == ("relative", "mother", "癫痫发作")
    assert (c.subject, c.subject_role, c.core) == ("relative", "sibling", "乳腺癌")
