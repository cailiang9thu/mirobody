"""§17.3 item 5: an indexed bgzip VCF is read per contig through pysam and gives the
same candidates as the plain text scan."""
import shutil
import time
from pathlib import Path

import pytest

from mirobody_rare.variant import get_clinvar, read_candidates, read_genotypes_at
from mirobody_rare.variant.vcf import has_index

VCF = Path("/data/xfs_recovery/caill/haenv-rare/derived/rare_attachments/JD-50/proband.vcf.gz")
MOM = Path("/data/xfs_recovery/caill/haenv-rare/derived/rare_attachments/JD-50/mother.vcf.gz")


@pytest.fixture(scope="module")
def indexed(tmp_path_factory):
    import pysam
    d = tmp_path_factory.mktemp("tbx")
    import gzip
    def bgzip(src, dst):
        plain = dst.with_suffix("")                      # haenv writes plain gzip; tabix wants bgzip
        with gzip.open(src, "rb") as fi, open(plain, "wb") as fo:
            shutil.copyfileobj(fi, fo)
        pysam.tabix_compress(str(plain), str(dst), force=True)
        pysam.tabix_index(str(dst), preset="vcf", force=True)
        plain.unlink()
    out = d / "proband.vcf.gz"; bgzip(VCF, out)
    mom = d / "mother.vcf.gz"; bgzip(MOM, mom)
    return out, mom


def test_indexed_and_text_paths_agree(indexed):
    out, mom = indexed
    assert has_index(out) and not has_index(VCF)
    cv = get_clinvar()
    t0 = time.perf_counter(); a, ca = read_candidates(VCF, cv, sex="M"); t_text = time.perf_counter() - t0
    t0 = time.perf_counter(); b, cb = read_candidates(out, cv, sex="M"); t_idx = time.perf_counter() - t0
    assert [x.key for x in a] == [x.key for x in b] and [x.gt for x in a] == [x.gt for x in b]
    assert ca["n_records"] == cb["n_records"]
    keys = {x.key for x in a}
    assert read_genotypes_at(MOM, keys) == read_genotypes_at(mom, keys)
    print(f"text {t_text:.2f}s indexed {t_idx:.2f}s")
