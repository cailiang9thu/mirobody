# 入库往返抽样对照 · 20260922-095441

case:JD-50

## 文件 · JD-50

| 入库前 | 入库后 | 差异 |
|---|---|---|
| mother: mother.vcf.gz sha256 4a506aaff238… 14455130 B | th_files#944 content_hash 4a506aaff238… bytes verified | 相同 |
| ped: JD-50.ped sha256 d8a492f6b59c… 189 B | th_files#937 content_hash d8a492f6b59c… bytes verified | 相同 |

共 8 条,抽样 2 条:相同 8 · 设计性差异 0 · 真差异 0

## 样本 · JD-50

| 入库前 | 入库后 | 差异 |
|---|---|---|
| proband: HG002 GRCh38 | sample#562 HG002 GRCh38 ready | 相同 |
| mother: HG004 GRCh38 | sample#564 HG004 GRCh38 ready | 相同 |

共 3 条,抽样 2 条:相同 3 · 设计性差异 0 · 真差异 0

## 变异 · JD-50

| 入库前 | 入库后 | 差异 |
|---|---|---|
| 14:50269318:G:A GT 1/1 L2HGDH (真值) | th_variant#1092 1/1 hom L2HGDH | 相同 |
| 10:70598447:C:T GT 0/1 PRF1 | th_variant#1093 0/1 het PRF1 | 相同 |
| 10:70598447:C:T GT 0/1 PRF1 | th_variant#1097 0/1 het PRF1 | 相同 |

共 11 条,抽样 3 条:相同 8 · 设计性差异 3 · 真差异 0

## 注释 · JD-50

| 入库前 | 入库后 | 差异 |
|---|---|---|
| ClinVar Pathogenic ★2 clinvar_grch38_plp:2026-09-20 | Pathogenic clinvar_grch38_plp:2026-09-20; gnomAD af_popmax=3.294096978215038e-05 | 相同 |
| ClinVar Pathogenic ★2 clinvar_grch38_plp:2026-09-20 | Pathogenic clinvar_grch38_plp:2026-09-20; gnomAD af_popmax=3.5970899542270303e-06 | 相同 |

共 8 条,抽样 2 条:相同 8 · 设计性差异 0 · 真差异 0

## 遗传来源 · JD-50

| 入库前 | 入库后 | 差异 |
|---|---|---|
| 期望 biparental(PED+父母 GT) | biparental is_de_novo=False | 相同 |

共 1 条,抽样 1 条:相同 1 · 设计性差异 0 · 真差异 0

## 家系 · JD-50

| 入库前 | 入库后 | 差异 |
|---|---|---|
| PED 3 members; accounts for 3 | th_pedigree JD-50: 3 members, 3 linked to accounts, analysis_only=['JD-50-F', 'JD-50-M'] | 相同 |

共 1 条,抽样 1 条:相同 1 · 设计性差异 0 · 真差异 0

## 影像 · JD-50

| 入库前 | 入库后 | 差异 |
|---|---|---|
| 3 series (TCIA UPENN-GBM) | 3 th_signal_object rows, deid=['done', 'done', 'done'], modality=['MRI', 'MRI', 'MRI'] | 相同 |

共 1 条,抽样 1 条:相同 1 · 设计性差异 0 · 真差异 0

## 表型 · JD-50

| 入库前 | 入库后 | 差异 |
|---|---|---|
| 「同期自述神经系统的肿瘤。」→ HP:0004375 神经系统的肿瘤 present/proband | th_phenotype#1443 HP:0004375 神经系统的肿瘤 negated=False proband/ src=nlp §现病史 | 相同 |
| 「起病初出现传染性脑炎。」→ HP:0002383 传染性脑炎 present/proband | th_phenotype#1437 HP:0002383 传染性脑炎 negated=False proband/ src=nlp §现病史 | 相同 |

共 11 条,抽样 2 条:相同 11 · 设计性差异 0 · 真差异 0

## 诊断 · JD-50

| 入库前 | 入库后 | 差异 |
|---|---|---|
| 金标 ORPHA:79314 | th_disease_code ['ORPHA:79314'] | 相同 |

共 1 条,抽样 1 条:相同 1 · 设计性差异 0 · 真差异 0

## 权限 · JD-50

| 入库前 | 入库后 | 差异 |
|---|---|---|
| 父亲行经工具读(access=0) | error denied(预期拒) | 权限 |
| 本人行经工具读 | ok 3 rows of 3 | 相同 |

共 2 条,抽样 2 条:相同 1 · 设计性差异 1 · 真差异 0

## 缺口

- th_disease_code 保留历史:表型排序的候选行不删,变异促升另加一行 source=nlp+variant(JD-77 因而并列 ORPHA:45448 与 ORPHA:70);读者按 source 取最新
- th_signal_object.file_id 现为 0(DICOM 处理器拿不到 th_files.id),影像层按 th_files.content_hash 校验字节、按行数与 deid 状态比对
- 表型层的"原始"是 haenv 合成句子,不是真实病历;真实语料读数待 D9 金标
- 样本层未比 bcftools stats(口径改为候选集,见 rare-mvp-testing 2.2)
