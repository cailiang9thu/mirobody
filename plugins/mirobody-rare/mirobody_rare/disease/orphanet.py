"""Orphanet tables → one cached JSON bundle (plan §D8).

Sources (all under `ontology_dir`, none read at runtime after the first build):
  en_product1.xml   disorder name / synonyms / ICD-10 / OMIM cross-references
  en_product6.xml   disorder ↔ gene associations (HGNC symbol + association type)
  phenotype.hpoa    ORPHA rows: disorder → HPO term + frequency
"""

from __future__ import annotations

import gzip
import json
import logging
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from .._config import cache_dir, ontology_dir

log = logging.getLogger(__name__)

BUNDLE_NAME = "orphanet_bundle.json.gz"

# HPO frequency terms → point estimate (hpoa `frequency` column)
FREQ = {"HP:0040280": 1.0, "HP:0040281": 0.9, "HP:0040282": 0.55, "HP:0040283": 0.17,
        "HP:0040284": 0.025, "HP:0040285": 0.0}


def freq_value(s: str) -> float | None:
    s = (s or "").strip()
    if not s:
        return None
    if s in FREQ:
        return FREQ[s]
    if s.endswith("%"):
        try:
            return float(s[:-1]) / 100.0
        except ValueError:
            return None
    if "/" in s:
        a, b = s.split("/", 1)
        try:
            return float(a) / float(b) if float(b) else None
        except ValueError:
            return None
    return None


@dataclass
class OrphaBundle:
    name: dict[str, str] = field(default_factory=dict)             # orpha -> English name
    synonyms: dict[str, list[str]] = field(default_factory=dict)
    icd10: dict[str, list[str]] = field(default_factory=dict)
    omim: dict[str, list[str]] = field(default_factory=dict)
    dtype: dict[str, str] = field(default_factory=dict)            # Disease / Malformation syndrome / ...
    genes: dict[str, list[dict]] = field(default_factory=dict)     # orpha -> [{symbol, hgnc, assoc}]
    annots: dict[str, list[tuple[str, float | None]]] = field(default_factory=dict)  # orpha -> [(hpo, freq)]
    meta: dict = field(default_factory=dict)


def _parse_product1(path: Path, b: OrphaBundle) -> None:
    for _, el in ET.iterparse(path, events=("end",)):
        if el.tag != "Disorder":
            continue
        code = (el.findtext("OrphaCode") or "").strip()
        if code:
            b.name[code] = (el.findtext("Name") or "").strip()
            b.synonyms[code] = [s.text.strip() for s in el.iterfind("SynonymList/Synonym") if s.text]
            b.dtype[code] = (el.findtext("DisorderType/Name") or "").strip()
            icd, om = [], []
            for ref in el.iterfind("ExternalReferenceList/ExternalReference"):
                src, r = (ref.findtext("Source") or "").strip(), (ref.findtext("Reference") or "").strip()
                if src == "ICD-10" and r:
                    icd.append(r)
                elif src == "OMIM" and r:
                    om.append(r)
            b.icd10[code], b.omim[code] = icd, om
        el.clear()


def _parse_product6(path: Path, b: OrphaBundle) -> None:
    for _, el in ET.iterparse(path, events=("end",)):
        if el.tag != "Disorder":
            continue
        code = (el.findtext("OrphaCode") or "").strip()
        rows = []
        for a in el.iterfind("DisorderGeneAssociationList/DisorderGeneAssociation"):
            g = a.find("Gene")
            if g is None:
                continue
            sym = (g.findtext("Symbol") or "").strip()
            hgnc = ""
            for ref in g.iterfind("ExternalReferenceList/ExternalReference"):
                if (ref.findtext("Source") or "").strip() == "HGNC":
                    hgnc = (ref.findtext("Reference") or "").strip()
            assoc = (a.findtext("DisorderGeneAssociationType/Name") or "").strip()
            if sym:
                rows.append({"symbol": sym, "hgnc": hgnc, "assoc": assoc})
        if code and rows:
            b.genes[code] = rows
        el.clear()


def _parse_hpoa(path: Path, b: OrphaBundle) -> None:
    with path.open(encoding="utf-8") as fh:
        head = None
        for line in fh:
            if line.startswith("#"):
                continue
            cols = line.rstrip("\n").split("\t")
            if head is None:
                head = {h: i for i, h in enumerate(cols)}
                continue
            db = cols[head["database_id"]]
            if not db.startswith("ORPHA:"):
                continue
            if cols[head["aspect"]] != "P":            # phenotypic abnormality only (not inheritance/course)
                continue
            if cols[head["qualifier"]].strip() == "NOT":
                continue
            code = db.split(":", 1)[1]
            b.annots.setdefault(code, []).append((cols[head["hpo_id"]], freq_value(cols[head["frequency"]])))


def build_orpha(src: Path | None = None, out: Path | None = None) -> Path:
    src = src or ontology_dir()
    out = out or (cache_dir() / BUNDLE_NAME)
    t0 = time.time()
    b = OrphaBundle()
    _parse_product1(src / "en_product1.xml", b)
    _parse_product6(src / "en_product6.xml", b)
    _parse_hpoa(src / "phenotype.hpoa", b)
    b.meta = {"built_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "n_disorders": len(b.name),
              "n_with_genes": len(b.genes), "n_annotated": len(b.annots)}
    doc = {"name": b.name, "synonyms": b.synonyms, "icd10": b.icd10, "omim": b.omim, "dtype": b.dtype,
           "genes": b.genes, "annots": b.annots, "meta": b.meta}
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False)
    log.info("[orpha] bundle built: %s in %.1fs -> %s", b.meta, time.time() - t0, out)
    return out


def load_orpha(path: Path | None = None) -> OrphaBundle:
    path = path or (cache_dir() / BUNDLE_NAME)
    if not path.exists():
        build_orpha(out=path)
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        doc = json.load(fh)
    b = OrphaBundle(**{k: doc[k] for k in ("name", "synonyms", "icd10", "omim", "dtype", "genes", "meta")})
    b.annots = {k: [(h, f) for h, f in v] for k, v in doc["annots"].items()}
    return b
