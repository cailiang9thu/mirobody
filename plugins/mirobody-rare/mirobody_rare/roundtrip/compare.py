"""Pure comparison helpers (no I/O): normalisation, per-layer diffs, six-way classification, truncation."""

from __future__ import annotations

KINDS = ("same", "design", "transport", "version", "order", "permission", "coding")


def norm_chrom(c) -> str:
    return str(c).replace("chr", "").replace("Chr", "")


def norm_gt(gt) -> str:
    return str(gt or "").replace("|", "/")


def norm_key(chrom, pos, ref, alt) -> str:
    return f"{norm_chrom(chrom)}:{int(pos)}:{ref}:{alt}"


def truncate(s: str, n: int = 600) -> str:
    s = str(s)
    return s if len(s) <= n else s[:n] + f"…(+{len(s) - n} chars)"


def classify(*, sha_ok: bool = True, version_ok: bool = True, order_ok: bool = True, permitted: bool = True,
             design: bool = False, equal: bool = True) -> str:
    """One label per compared item, in the order a reader should suspect causes."""
    if not sha_ok:
        return "transport"
    if not version_ok:
        return "version"
    if not order_ok:
        return "order"
    if not permitted:
        return "permission"
    if design:
        return "design"
    return "same" if equal else "coding"


def compare_variants(original: list[tuple], stored: list[dict], min_stars: int = 1) -> dict:
    """`original`: (chrom, pos, ref, alt, gt, stars) from `read_candidates` on the source file;
    `stored`: th_variant rows. Rows the filter was designed to drop (stars < min_stars) count as
    `design`, never as missing."""
    exp = {norm_key(c, p, r, a): (norm_gt(g), st) for c, p, r, a, g, st in original}
    got = {norm_key(v["chrom"], v["pos"], v["ref"], v["alt"]): norm_gt(v.get("genotype")) for v in stored}
    same = design = 0
    missing, gt_diff = [], []
    for k, (g, st) in exp.items():
        if st < min_stars:
            design += 1 if k not in got else 0
            if k in got:
                same += 1
            continue
        if k not in got:
            missing.append(k)
        elif got[k] != g:
            gt_diff.append((k, g, got[k]))
        else:
            same += 1
    extra = sorted(k for k in got if k not in exp)
    return {"same": same, "design": design, "missing": missing, "gt_diff": gt_diff, "extra": extra}


def compare_sets(expected: set, stored: set) -> dict:
    return {"same": len(expected & stored), "missing": sorted(expected - stored), "extra": sorted(stored - expected)}
