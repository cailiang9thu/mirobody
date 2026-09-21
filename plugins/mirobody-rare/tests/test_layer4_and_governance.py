import glob
import io
import zipfile

import pytest

from mirobody_rare.consent import permit
from mirobody_rare.ingest import ingest_dicom, ingest_ped, ingest_vcf
from mirobody_rare.repo import MemoryRepo
from mirobody_rare.signal import index_dicom_zip
from mirobody_rare.tools import RareQueryService

STS = sorted(glob.glob("/data/xfs_recovery/data/rare/03_dicom/Soft-tissue-Sarcoma/STS_027/*.zip"))
VCF = "/data/xfs_recovery/caill/haenv-rare/derived/rare_attachments/JD-55/proband.vcf.gz"
PED = "/data/xfs_recovery/caill/haenv-rare/derived/rare_attachments/JD-50/JD-50.ped"


def _dicom_zip_with_serial(tmp_path):
    import pydicom
    from pydicom.dataset import FileDataset, FileMetaDataset
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = pydicom.uid.MRImageStorage
    meta.MediaStorageSOPInstanceUID = pydicom.uid.generate_uid()
    meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian
    ds = FileDataset("x.dcm", {}, file_meta=meta, preamble=b"\0" * 128)
    ds.PatientName = "P-1"
    ds.PatientID = "P-1"
    ds.DeviceSerialNumber = "SN-777"
    ds.Modality = "MR"
    buf = io.BytesIO()
    ds.save_as(buf, enforce_file_format=True)
    z = tmp_path / "serial.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("00000001.dcm", buf.getvalue())
    return z


def test_context_tag_scrubbed_not_failed(tmp_path):
    idx = index_dicom_zip(_dicom_zip_with_serial(tmp_path))
    assert idx.deid_status == "done" and idx.scrubbed_tags == ["DeviceSerialNumber"]
    assert "SN-777" not in str(idx.to_dict()) and "P-1" not in str(idx.to_dict())


def _dicom_zip_with_phi(tmp_path):
    import pydicom
    from pydicom.dataset import FileDataset, FileMetaDataset
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = pydicom.uid.MRImageStorage
    meta.MediaStorageSOPInstanceUID = pydicom.uid.generate_uid()
    meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian
    ds = FileDataset("x.dcm", {}, file_meta=meta, preamble=b"\0" * 128)
    ds.PatientName = "Zhang^San"
    ds.PatientID = "MRN-0042"
    ds.PatientBirthDate = "19800101"
    ds.Modality = "MR"
    ds.BodyPartExamined = "BRAIN"
    ds.StudyDate = "20240101"
    buf = io.BytesIO()
    ds.save_as(buf, enforce_file_format=True)
    z = tmp_path / "phi.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("00000001.dcm", buf.getvalue())
    return z


@pytest.mark.skipif(not STS, reason="TCIA sample not on disk")
def test_dicom_index_whitelist():
    idx = index_dicom_zip(STS[0])
    assert idx.deid_status == "done" and idx.modality == "RTSTRUCT" and idx.body_part == "EXTREMITY"
    d = idx.to_dict()
    assert "STS_027" not in str(d)            # patient id / name never leave the indexer


def test_dicom_index_phi_fails_without_leaking(tmp_path):
    idx = index_dicom_zip(_dicom_zip_with_phi(tmp_path))
    assert idx.deid_status == "failed" and set(idx.phi_tags) == {"PatientName", "PatientBirthDate"}
    d = idx.to_dict()
    assert "Zhang" not in str(d) and "MRN" not in str(d) and "BRAIN" not in str(d)


async def test_consent_gate_rules():
    repo = MemoryRepo()
    assert (await permit(repo, "u1", "u1", "variant")).allowed                       # self, individual return
    assert not (await permit(repo, "u1", "u2", "variant")).allowed                   # other, no shared circle
    repo.t["care_circle"].append({"operator": "u1", "subject": "u2", "access": 1})   # u2 shares (view) with u1
    assert (await permit(repo, "u1", "u2", "variant")).allowed                       # the circle is the consent
    assert not (await permit(repo, "u1", "u2", "variant", "research_use")).allowed  # scope not granted
    assert not (await permit(repo, "u1", "u2", "variant", cross_border=True)).allowed
    repo.t["th_pedigree"].append({"id": 1, "family_id": "F"})
    repo.t["th_pedigree_member"].append({"id": 1, "pedigree_id": 1, "user_id": "u2", "individual_id": "F-M", "analysis_only": True})
    d = await permit(repo, "u1", "u2", "variant")
    assert not d.allowed and d.reason.startswith("analysis_only")
    assert not (await permit(repo, "u2", "u2", "variant")).allowed                   # even to themselves


@pytest.mark.skipif(not STS, reason="TCIA sample not on disk")
async def test_tools_over_memory_repo(tmp_path):
    repo = MemoryRepo()
    svc = RareQueryService(repo)
    me = {"user_id": "u1"}
    await repo.add_phenotypes([{"user_id": "u1", "hpo_id": "HP:0001250", "hpo_label": "癫痫发作", "negated": False, "subject": "proband", "source": "nlp"},
                               {"user_id": "u1", "hpo_id": "HP:0000365", "hpo_label": "听力受损", "negated": True, "subject": "proband", "source": "nlp"}])
    r = await svc.query_phenotype(me, negated=True)
    assert r["status"] == "ok" and [x["hpo_id"] for x in r["data"]] == ["HP:0000365"] and r["assumptions"]
    await ingest_dicom(repo, "u1", STS[0], file_id=7)
    await ingest_dicom(repo, "u1", _dicom_zip_with_phi(tmp_path), file_id=8)
    r = await svc.query_signal_index(me)
    assert [x["file_id"] for x in r["data"]] == [7]                                   # pending/failed object hidden
    r = await svc.query_variant({"user_id": "u9"}, subject_id="u1")
    assert r["status"] == "error" and r["error_kind"] == "denied"


async def test_ingest_vcf_idempotent_and_ped():
    repo = MemoryRepo()
    res = await ingest_vcf(repo, "u1", VCF, sex="F")
    assert res["status"] == "ready" and res["kept"] >= 1 and res["shards"] >= 1
    genes = {v["gene_symbol"] for v in await repo.variants("u1")}
    assert "TP53" in genes
    anns = repo.t["th_variant_annotation"]
    assert anns and anns[0]["source"] == "clinvar" and anns[0]["clinical_significance"].startswith("Pathogenic")
    n1 = len(repo.t["th_variant"])
    res2 = await ingest_vcf(repo, "u1", VCF, sex="F", sample_id=res["sample_id"])
    assert res2["shards"] == 0 and res2["skipped_shards"] >= 1 and len(repo.t["th_variant"]) == n1
    res3 = await ingest_vcf(repo, "u1", VCF, sex="F")                       # same bytes uploaded again
    assert res3.get("duplicate") and res3["sample_id"] == res["sample_id"] and len(repo.t["th_variant"]) == n1
    pid = await ingest_ped(repo, PED)
    fam = await repo.pedigree_of(None)
    members = [m for m in repo.t["th_pedigree_member"] if m["pedigree_id"] == pid]
    assert len(members) == 3 and sum(m["is_proband"] for m in members) == 1 and sum(m["analysis_only"] for m in members) == 2


async def test_ingest_vcf_refuses_wrong_build(tmp_path):
    import gzip
    p = tmp_path / "b37.vcf.gz"
    with gzip.open(p, "wt") as fh:
        fh.write("##fileformat=VCFv4.2\n##reference=GRCh37\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS\n")
    with pytest.raises(ValueError, match="GRCh38"):
        await ingest_vcf(MemoryRepo(), "u1", p)


class _CountingFile:
    """UploadFile look-alike that counts bytes handed out: a probe must not read the body."""

    def __init__(self, path):
        self.filename = path.name
        self.content_type = "application/zip"
        self._f = open(path, "rb")
        self.size = path.stat().st_size
        self.bytes_read = 0

    async def seek(self, pos, whence=0):
        self._f.seek(pos, whence)

    async def read(self, n=-1):
        b = self._f.read(n)
        self.bytes_read += len(b)
        return b

    def tell(self):
        return self._f.tell()


@pytest.mark.skipif(not STS, reason="TCIA sample not on disk")
async def test_dicom_probe_reads_header_only(tmp_path):
    from mirobody_rare.handlers import is_dicom_zip
    big = tmp_path / "big.zip"
    with zipfile.ZipFile(STS[0]) as src, zipfile.ZipFile(big, "w") as dst:
        for n in src.namelist():
            dst.writestr(n, src.read(n))
        dst.writestr("padding.bin", b"\0" * (24 * 1024 * 1024))       # 24 MB body the probe must skip
    f = _CountingFile(big)
    assert await is_dicom_zip(f) is True
    assert f.bytes_read < 256 * 1024, f.bytes_read


async def test_record_consent_then_permit():
    from mirobody_rare.consent import permit
    repo = MemoryRepo()
    svc = RareQueryService(repo)
    repo.t["care_circle"].append({"operator": "u1", "subject": "u2", "access": 1})
    assert not (await permit(repo, "u1", "u2", "variant", "research_use")).allowed
    r = await svc.record_consent({"user_id": "u2"}, scope="research_use", granted=True, layer="variant", relationship="self")
    assert r["status"] == "ok" and r["data"][0]["id"]
    assert (await permit(repo, "u1", "u2", "variant", "research_use")).allowed
    assert not (await permit(repo, "u1", "u2", "phenotype", "research_use")).allowed      # layer-scoped
    r = await svc.record_consent({"user_id": "u2"}, scope="bogus", granted=True)
    assert r["status"] == "error"
