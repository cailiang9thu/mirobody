"""§17.3 item 1: size and type are judged from `upload_start` metadata, before any chunk."""
from mirobody.collect.files.admission import DEFAULT_LIMITS, admit_files

GB = 1 << 30
MB = 1 << 20


def test_refuses_sequencing_raw_data_by_type():
    ok, reason = admit_files([{"filename": "sample.fastq.gz", "size": 10 * MB}], DEFAULT_LIMITS)
    assert not ok and "FASTQ" in reason.upper()
    ok, reason = admit_files([{"filename": "x.bam", "size": 1 * MB}], DEFAULT_LIMITS)
    assert not ok


def test_refuses_oversize_vcf_as_wgs():
    ok, reason = admit_files([{"filename": "genome.vcf.gz", "size": 3 * GB}], DEFAULT_LIMITS)
    assert not ok and "WGS" in reason
    ok, _ = admit_files([{"filename": "exome.vcf.gz", "size": 300 * MB}], DEFAULT_LIMITS)
    assert ok


def test_refuses_oversize_dicom_and_accepts_documents():
    ok, _ = admit_files([{"filename": "series.zip", "size": 6 * GB}], DEFAULT_LIMITS)
    assert not ok
    ok, _ = admit_files([{"filename": "labs.pdf", "size": 2 * MB}, {"filename": "notes.md", "size": 1000}], DEFAULT_LIMITS)
    assert ok


def test_unknown_size_is_admitted_and_limits_are_configurable():
    ok, _ = admit_files([{"filename": "x.vcf"}], DEFAULT_LIMITS)
    assert ok
    ok, _ = admit_files([{"filename": "x.vcf.gz", "size": 60 * MB}], {**DEFAULT_LIMITS, "vcf": 50 * MB})
    assert not ok
