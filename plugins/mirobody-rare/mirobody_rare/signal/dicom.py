"""D4 (plan §5): a DICOM series zip → one de-identified index record, `th_signal_object`-shaped.

Whitelist, not blacklist: only the tags named in `_KEEP` reach the record. Patient name,
patient id, birth date, institution, operator and referring physician are read only to
DECIDE `deid_status`; their values never leave this function, not even in an error string
(the failure names the tag, never what it held). Pixels are never touched
(`stop_before_pixels`)."""

from __future__ import annotations

import hashlib
import io
import logging
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

_MODALITY = {"MR": "MRI", "CT": "CT", "PT": "PET", "US": "US", "CR": "XR", "DX": "XR", "RTSTRUCT": "RTSTRUCT",
             "SEG": "SEG", "SM": "WSI"}
# Patient-identifying tags: any non-empty value (other than a pseudonymous name equal to the
# study's own patient id) FAILS de-identification and hides the object.
_PHI_PATIENT = ("PatientName", "PatientBirthDate", "OtherPatientIDs", "OtherPatientNames", "PatientAddress",
                "PatientTelephoneNumbers", "PatientMotherBirthName", "EthnicGroup")
# Context tags (PS3.15 Annex E basic profile, but not the patient's identity): never emitted;
# a non-empty value is recorded by TAG NAME under `scrubbed_tags` and the object stays indexed.
# TCIA keeps a dummy DeviceSerialNumber on MR series, which is exactly this class.
_PHI_CONTEXT = ("InstitutionName", "InstitutionAddress", "ReferringPhysicianName", "OperatorsName",
                "PerformingPhysicianName", "DeviceSerialNumber", "StationName")


@dataclass
class SignalIndex:
    modality: str = "OTHER"
    format: str = "DICOM"
    body_part: str | None = None
    study_date: str | None = None            # ISO date, as printed in the header (TCIA shifts dates)
    series_desc: str | None = None
    series_uid: str | None = None
    instance_count: int = 0
    manufacturer: str | None = None
    patient_sex: str | None = None
    deid_status: str = "pending"             # pending | done | failed
    deid_method: str | None = None
    phi_tags: list[str] = field(default_factory=list)   # tag NAMES that failed, never values
    scrubbed_tags: list[str] = field(default_factory=list)   # context tags that held a value and were dropped
    sha256: str | None = None
    path: str | None = None

    def to_row(self, user_id: str, file_id: int | None = None, residency: str = "CN") -> dict:
        """`th_signal_object` row. A failed object keeps only status: no metadata leaves it."""
        base = {"user_id": user_id, "file_id": file_id, "modality": self.modality, "format": self.format,
                "residency": residency, "exportable": False, "deid_status": self.deid_status}
        if self.deid_status != "done":
            return base
        return {**base, "body_part": self.body_part, "study_date": self.study_date, "series_desc": self.series_desc,
                "instance_count": self.instance_count}

    def to_dict(self) -> dict:
        # no `path`: on a TCIA-style tree the directory name IS the pseudonymous patient id,
        # and the index must not carry any identifier; sha256 is the pointer's identity
        d = {"modality": self.modality, "format": self.format, "deid_status": self.deid_status, "sha256": self.sha256,
             "instance_count": self.instance_count}
        if self.deid_status == "done":
            d.update({"body_part": self.body_part, "study_date": self.study_date, "series_desc": self.series_desc,
                      "series_uid": self.series_uid, "manufacturer": self.manufacturer, "patient_sex": self.patient_sex,
                      "deid_method": self.deid_method, "scrubbed_tags": self.scrubbed_tags})
        else:
            d["phi_tags"] = self.phi_tags
        return d


def _iso(d: str | None) -> str | None:
    s = (d or "").strip()
    if len(s) == 8 and s.isdigit():
        try:
            return date(int(s[:4]), int(s[4:6]), int(s[6:])).isoformat()
        except ValueError:
            return None
    return None


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def index_dicom_zip(path: str | Path, max_instances: int = 5) -> SignalIndex:
    """Index one series zip. Reads headers of up to `max_instances` files for the PHI decision
    (every instance carries the patient module) and the first for the series metadata."""
    import pydicom

    p = Path(path)
    idx = SignalIndex(path=str(p), sha256=_sha256(p))
    try:
        zf = zipfile.ZipFile(p)
    except zipfile.BadZipFile:
        idx.deid_status = "failed"
        idx.phi_tags = ["bad_zip"]
        return idx
    names = [n for n in zf.namelist() if n.lower().endswith(".dcm")]
    idx.instance_count = len(names)
    if not names:
        idx.deid_status = "failed"
        idx.phi_tags = ["no_dicom_instances"]
        return idx
    phi: set[str] = set()
    scrubbed: set[str] = set()
    first = None
    for n in names[:max_instances]:
        ds = pydicom.dcmread(io.BytesIO(zf.read(n)), stop_before_pixels=True)
        first = first or ds
        pid = str(ds.get("PatientID", "") or "").strip()
        for t in _PHI_PATIENT:
            v = str(ds.get(t, "") or "").strip()
            if not v:
                continue
            if t == "PatientName" and v == pid:      # pseudonymous name == pseudonymous id: allowed
                continue
            phi.add(t)
        for t in _PHI_CONTEXT:
            if str(ds.get(t, "") or "").strip():
                scrubbed.add(t)
    ds = first
    idx.deid_method = str(ds.get("DeidentificationMethod", "") or "") or None
    removed = str(ds.get("PatientIdentityRemoved", "") or "").upper() == "YES"
    idx.modality = _MODALITY.get(str(ds.get("Modality", "") or ""), "OTHER")
    if phi:
        idx.deid_status = "failed"
        idx.phi_tags = sorted(phi)
        return idx
    idx.deid_status = "done"                     # patient identity clean; the whitelist drops everything else
    idx.scrubbed_tags = sorted(scrubbed)
    idx.body_part = str(ds.get("BodyPartExamined", "") or "") or None
    idx.study_date = _iso(str(ds.get("StudyDate", "") or ""))
    idx.series_desc = str(ds.get("SeriesDescription", "") or "") or None
    idx.series_uid = str(ds.get("SeriesInstanceUID", "") or "") or None
    idx.manufacturer = str(ds.get("Manufacturer", "") or "") or None
    idx.patient_sex = str(ds.get("PatientSex", "") or "") or None
    return idx
