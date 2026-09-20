from mirobody_rare.assertion import extract


def test_negation_prefix():
    (a,) = extract("未见听力受损")
    assert a.polarity == "absent" and a.core == "听力受损" and a.subject == "proband"


def test_packed_negation_expands():
    out = extract("肝脾肿大、黄疸、腹水均正常")
    assert [a.polarity for a in out] == ["absent"] * 3
    assert [a.core for a in out] == ["肝脾肿大", "黄疸", "腹水"]


def test_relative_subject():
    (a,) = extract("姐姐有癫痫发作史")
    assert a.subject == "relative" and a.subject_role == "sibling" and a.core == "癫痫发作"
    (b,) = extract("母亲也出现过肌张力障碍")
    assert b.subject_role == "mother" and b.core == "肌张力障碍"


def test_present_template_noise_stripped():
    assert extract("癫痫发作较明显")[0].core == "癫痫发作"
    assert extract("近期失语症")[0].core == "失语症"
    assert extract("自述共济失调")[0].asserted_by == "patient"


def test_onset_and_prior():
    a, b = extract("1岁时出现剪刀样步态,外院诊断为脑瘫")
    assert a.onset_text == "1岁时" and "剪刀样步态" in a.core and a.asserted_by == "prior_clinician"
    assert b.kind == "diagnosis_hypothesis"
