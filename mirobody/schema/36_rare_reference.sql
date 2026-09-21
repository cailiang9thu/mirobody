-- Coding reference tables for the rare-disease layer (docs/rare-mvp-plan.md §16.3).
-- Loaded by `mirobody-rare-load-reference` from the sources registered in res/EXTERNAL.tsv;
-- no data in git or in the wheel. Every table carries source_version (ClinVar is monthly,
-- HPO / Orphanet quarterly) so th_variant_annotation.source_version can be traced back.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- A btree row may not exceed ~2.7 kB and ClinVar carries deletions/insertions longer than that,
-- so the identity is an md5 of the five-tuple (generated column, primary key); the (chrom,pos)
-- index serves region scans. Lookups compute the same md5 over unnested query tuples.
CREATE TABLE IF NOT EXISTS ref_clinvar (
    chrom           VARCHAR(8)  NOT NULL,          -- no chr prefix, GRCh38
    pos             INTEGER     NOT NULL,
    ref             TEXT        NOT NULL,
    alt             TEXT        NOT NULL,
    vkey            CHAR(32)    GENERATED ALWAYS AS (md5(chrom || ':' || pos::text || ':' || ref || ':' || alt)) STORED,
    variation_id    VARCHAR(16) NOT NULL,
    clnsig          VARCHAR(64) NOT NULL,
    review_status   VARCHAR(80),
    stars           SMALLINT    NOT NULL DEFAULT 0,
    gene_symbol     VARCHAR(64),
    consequence     VARCHAR(64),
    condition_names TEXT[],
    disease_db      TEXT,                          -- CLNDISDB verbatim (Orphanet:/OMIM: links)
    source_version  VARCHAR(32) NOT NULL,
    PRIMARY KEY (vkey)
);
CREATE INDEX IF NOT EXISTS idx_ref_clinvar_locus ON ref_clinvar (chrom, pos);
CREATE INDEX IF NOT EXISTS idx_ref_clinvar_gene  ON ref_clinvar (gene_symbol);

CREATE TABLE IF NOT EXISTS ref_hgnc (
    symbol          VARCHAR(64) PRIMARY KEY,
    hgnc_id         VARCHAR(16) NOT NULL,
    name            TEXT,
    location        VARCHAR(64),
    source_version  VARCHAR(32) NOT NULL
);
CREATE TABLE IF NOT EXISTS ref_hgnc_alias (
    alias           VARCHAR(64) NOT NULL,
    symbol          VARCHAR(64) NOT NULL REFERENCES ref_hgnc(symbol),
    kind            VARCHAR(8)  NOT NULL,          -- alias | prev
    PRIMARY KEY (alias, symbol)
);
CREATE TABLE IF NOT EXISTS ref_gene_hpo (
    gene_symbol     VARCHAR(64) NOT NULL,
    hpo_id          VARCHAR(16) NOT NULL,
    disease_id      VARCHAR(32) NOT NULL DEFAULT '',
    source_version  VARCHAR(32) NOT NULL,
    PRIMARY KEY (gene_symbol, hpo_id, disease_id)
);
CREATE INDEX IF NOT EXISTS idx_ref_gene_hpo_disease ON ref_gene_hpo (disease_id);

CREATE TABLE IF NOT EXISTS ref_orpha_disorder (
    orpha_code      VARCHAR(16) PRIMARY KEY,
    name            TEXT NOT NULL,
    disorder_type   VARCHAR(64),
    icd10           TEXT[],
    omim            TEXT[],
    source_version  VARCHAR(32) NOT NULL
);
CREATE TABLE IF NOT EXISTS ref_orpha_name (
    name_key        TEXT NOT NULL,                 -- lexical.normalize(name)
    orpha_code      VARCHAR(16) NOT NULL REFERENCES ref_orpha_disorder(orpha_code),
    kind            VARCHAR(8)  NOT NULL,          -- label | synonym
    PRIMARY KEY (name_key, orpha_code)
);
CREATE INDEX IF NOT EXISTS idx_ref_orpha_name_trgm ON ref_orpha_name USING gin (name_key gin_trgm_ops);
CREATE TABLE IF NOT EXISTS ref_orpha_gene (
    orpha_code      VARCHAR(16) NOT NULL REFERENCES ref_orpha_disorder(orpha_code),
    gene_symbol     VARCHAR(64) NOT NULL,
    hgnc_id         VARCHAR(16),
    association     VARCHAR(80),
    PRIMARY KEY (orpha_code, gene_symbol)
);
CREATE TABLE IF NOT EXISTS ref_orpha_hpo (
    orpha_code      VARCHAR(16) NOT NULL REFERENCES ref_orpha_disorder(orpha_code),
    hpo_id          VARCHAR(16) NOT NULL,
    frequency       REAL,
    PRIMARY KEY (orpha_code, hpo_id)
);
CREATE INDEX IF NOT EXISTS idx_ref_orpha_hpo_term ON ref_orpha_hpo (hpo_id);
