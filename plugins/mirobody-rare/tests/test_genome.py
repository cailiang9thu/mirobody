import gzip

import pytest

from mirobody_rare.pedigree import parse_ped, trio_inheritance
from mirobody_rare.variant.vcf import _zygosity, read_candidates, read_genotypes_at


class _CV:
    def __init__(self, keys):
        self.keys = keys

    def lookup(self, chrom, pos, ref, alt):
        k = f"{str(chrom).replace('chr', '')}:{pos}:{ref}:{alt}"
        return {"vid": "1", "clnsig": "Pathogenic", "rev": "criteria_provided,_multiple_submitters,_no_conflicts",
                "stars": 2, "gene": "GENEX", "dn": [], "mc": "missense_variant"} if k in self.keys else None

    def lookup_many(self, keys):
        return {k: r for k in keys if (r := self.lookup(*k))}


def _vcf(tmp_path, rows, name="p.vcf.gz"):
    p = tmp_path / name
    with gzip.open(p, "wt") as fh:
        fh.write("##fileformat=VCFv4.2\n##reference=GRCh38\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS\n")
        for r in rows:
            fh.write("\t".join(map(str, r)) + "\n")
    return p


def test_candidates_filter_and_zygosity(tmp_path):
    p = _vcf(tmp_path, [("chr1", 100, ".", "A", "G", 50, "PASS", ".", "GT", "0/1"),
                        ("chr1", 200, ".", "C", "T", 50, "LowQual", ".", "GT", "1/1"),
                        ("chr1", 300, ".", "G", "A", 50, "PASS", ".", "GT", "0/0"),
                        ("chrX", 400, ".", "T", "TG", 50, "PASS", ".", "GT", "1")])
    cands, counts = read_candidates(p, _CV({"1:100:A:G", "1:200:C:T", "1:300:G:A", "X:400:T:TG"}), sex="M")
    assert [c.key for c in cands] == ["1:100:A:G", "X:400:T:TG"]
    assert cands[0].zygosity == "het" and cands[1].zygosity == "hemi"
    assert counts["n_pass"] == 3 and counts["n_nonref"] == 2
    assert _zygosity("1/1", "chr2", "F") == "hom"


def test_parent_lookup_and_trio(tmp_path):
    f = _vcf(tmp_path, [("chr1", 100, ".", "A", "G", 50, "PASS", ".", "GT", "0/0")], "f.vcf.gz")
    got = read_genotypes_at(f, {"1:100:A:G", "1:999:A:C"})
    assert got == {"1:100:A:G": "0/0"}
    assert trio_inheritance("0/0", "0/0") == "de_novo"
    assert trio_inheritance("0/1", "0/1") == "biparental"
    assert trio_inheritance("0", "0/1") == "maternal"
    assert trio_inheritance(None, "0/1") == "unknown"   # father not covered: never de novo


def test_ped_roundtrip(tmp_path):
    p = tmp_path / "x.ped"
    p.write_text("# c\nF1\tF1-P\tF1-F\tF1-M\t1\t2\nF1\tF1-F\t0\t0\t1\t1\nF1\tF1-M\t0\t0\t2\t1\n")
    pg = parse_ped(p)
    pb = pg.proband()
    assert pb.individual_id == "F1-P" and pb.sex_code == "M"
    fam, rows = pg.to_rows()
    assert fam["family_id"] == "F1" and sum(r["is_proband"] for r in rows) == 1
    assert all(r["analysis_only"] for r in rows if not r["is_proband"])
