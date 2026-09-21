"""Upload admission: decide from `upload_start` metadata, before any chunk is received,
whether a file may come in at all (rare-mvp-plan §17.3 item 1).

Pure function, no I/O: `admit_files(files_info, limits)` -> (ok, reason). The limits are a
plain mapping so a deployment can override them from config (`UPLOAD_MAX_BYTES`), and the
reason is written for the person who tried the upload, naming what IS supported. A refusal
here costs nothing; a refusal after 3 GB have been buffered costs 3 GB of memory.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

GB = 1 << 30
MB = 1 << 20

#: byte ceilings per kind; a kind absent here has no ceiling (documents, images)
DEFAULT_LIMITS: dict[str, int] = {
    "vcf": 2 * GB,          # gVCF / multi-sample joint calls; beyond this it is WGS (phase 2)
    "dicom": 5 * GB,
    "ped": 10 * MB,
}

#: extensions refused outright, with the reason shown to the uploader
_REFUSED_KINDS: dict[str, str] = {
    "fastq": "FASTQ raw sequencing reads are not accepted: upload the called variants (VCF, GRCh38) instead",
    "fq": "FASTQ raw sequencing reads are not accepted: upload the called variants (VCF, GRCh38) instead",
    "bam": "BAM alignments are not accepted: upload the called variants (VCF, GRCh38) instead",
    "cram": "CRAM alignments are not accepted: upload the called variants (VCF, GRCh38) instead",
    "edf": "EDF / EEG recordings are not accepted yet (phase 2)",
}


def kind_of(filename: str) -> str:
    """vcf | ped | dicom | fastq | bam | cram | edf | other, from the name alone."""
    n = (filename or "").lower()
    for ext in (".gz", ".bgz", ".zip"):
        if n.endswith(ext):
            n = n[: -len(ext)]
            break
    if n.endswith((".vcf", ".gvcf", ".g.vcf")):
        return "vcf"
    if n.endswith(".ped"):
        return "ped"
    if n.endswith((".dcm", ".dicom")) or (filename or "").lower().endswith(".zip"):
        return "dicom" if n.endswith((".dcm", ".dicom")) or "dicom" in n or "series" in n else "zip"
    for k in ("fastq", "fq", "bam", "cram", "edf"):
        if n.endswith("." + k):
            return k
    return "other"


def admit_files(files_info: Sequence[Mapping], limits: Mapping[str, int] | None = None) -> tuple[bool, str]:
    limits = dict(DEFAULT_LIMITS, **(limits or {}))
    for f in files_info or ():
        name = str(f.get("filename") or f.get("name") or "")
        kind = kind_of(name)
        if kind in _REFUSED_KINDS:
            return False, f"{name}: {_REFUSED_KINDS[kind]}"
        size = f.get("size")
        if not isinstance(size, (int, float)) or size <= 0:
            continue                                     # unknown size: admitted, judged again on disk
        cap = limits.get(kind) if kind != "zip" else limits.get("dicom")
        if cap is not None and size > cap:
            what = {"vcf": "a WGS-scale VCF (WES / panel only in this release)", "dicom": "an imaging archive",
                    "ped": "a pedigree file"}.get(kind if kind != "zip" else "dicom", "a file")
            return False, (f"{name}: {int(size) // MB} MB exceeds the {cap // MB} MB limit for {what}; "
                           f"contact the team for WGS or bulk imaging intake")
    return True, ""
