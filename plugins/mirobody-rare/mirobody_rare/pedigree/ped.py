"""PED import (plan §6.2 / §6.3) and the trio inheritance call for a proband variant.
Absent parent data yields `unknown`, never `de_novo`: a de novo call needs BOTH parents
covered at the locus and reference there."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PedMember:
    family_id: str
    individual_id: str
    paternal_id: str | None
    maternal_id: str | None
    sex: int | None          # 1 male, 2 female, None unknown
    affected: int | None     # 1 unaffected, 2 affected, None unknown

    @property
    def sex_code(self) -> str | None:
        return {1: "M", 2: "F"}.get(self.sex or 0)


@dataclass
class Pedigree:
    family_id: str
    members: list[PedMember] = field(default_factory=list)

    def proband(self) -> PedMember | None:
        aff = [m for m in self.members if m.affected == 2]
        with_parents = [m for m in aff if m.paternal_id or m.maternal_id]
        return (with_parents or aff or self.members or [None])[0]

    def parents_of(self, m: PedMember) -> dict[str, PedMember | None]:
        by = {x.individual_id: x for x in self.members}
        return {"father": by.get(m.paternal_id or ""), "mother": by.get(m.maternal_id or "")}

    def to_rows(self) -> tuple[dict, list[dict]]:
        """`th_pedigree` + `th_pedigree_member` rows (plan §6.2). Non-proband members are
        `analysis_only`: they take part in the computation and get no individual conclusion."""
        pb = self.proband()
        rows = [{"individual_id": m.individual_id, "paternal_id": m.paternal_id, "maternal_id": m.maternal_id,
                 "sex": m.sex, "affected": m.affected, "is_proband": m is pb, "analysis_only": m is not pb}
                for m in self.members]
        return {"family_id": self.family_id}, rows


def map_ped_to_circle(pg: Pedigree, members: list[dict], uploader_id: str) -> dict[str, str]:
    """PED individual ids → account ids. The proband is the uploader; every other member
    is matched against the uploader's care-circle members by nickname, name, email or id.
    Unmatched relatives stay unmapped (no account yet), never guessed."""
    out: dict[str, str] = {}
    pb = pg.proband()
    if pb:
        out[pb.individual_id] = str(uploader_id)
    idx: dict[str, str] = {}
    for m in members:
        uid = str(m.get("user_id") or "")
        if not uid or uid == str(uploader_id):
            continue
        for key in (m.get("nickname"), m.get("name"), m.get("email"), uid):
            if key:
                idx.setdefault(str(key).strip().lower(), uid)
    for m in pg.members:
        if m.individual_id in out:
            continue
        uid = idx.get(m.individual_id.strip().lower())
        if uid:
            out[m.individual_id] = uid
    return out


def parse_ped(path: str | Path) -> Pedigree:
    members: list[PedMember] = []
    fam = ""
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        c = line.split()
        if len(c) < 6:
            continue
        fam = fam or c[0]
        members.append(PedMember(c[0], c[1], None if c[2] == "0" else c[2], None if c[3] == "0" else c[3],
                                 int(c[4]) if c[4] in ("1", "2") else None,
                                 int(c[5]) if c[5] in ("1", "2") else None))
    return Pedigree(family_id=fam, members=members)


def _carries(gt: str | None) -> bool | None:
    if gt is None or gt in ("./.", "."):
        return None
    return any(a not in ("0", ".") for a in gt.replace("|", "/").split("/"))


def trio_inheritance(father_gt: str | None, mother_gt: str | None) -> str:
    f, m = _carries(father_gt), _carries(mother_gt)
    if f is None or m is None:
        return "unknown"
    if f and m:
        return "biparental"
    if f:
        return "paternal"
    if m:
        return "maternal"
    return "de_novo"
