-- Layer 2 of the rare-disease MVP (docs/rare-mvp-plan.md §4.2): sequencing samples,
-- variant calls and their versioned annotations. Append-only, replayable. The plan named
-- this `a7_variant.sql`; 1.5.0 prefixes are two digits (schema/README.md).
--
-- `th_series_data_genetic` (consumer arrays, rsid/genotype) is untouched: an array probe
-- and a sequencing call are different data with different legal weight.
CREATE TABLE IF NOT EXISTS th_sequencing_sample (
    id            INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id       VARCHAR(200) NOT NULL,
    file_id       BIGINT,                          -- th_files, the raw VCF
    assay         VARCHAR(16)  NOT NULL,           -- WES | WGS | PANEL | RNASEQ
    reference     VARCHAR(16)  NOT NULL,           -- GRCh37 | GRCh38, never omitted
    sample_label  VARCHAR(128),                    -- the VCF sample column
    caller        VARCHAR(64),
    called_at     DATE,
    status        VARCHAR(16) NOT NULL DEFAULT 'pending',  -- pending|parsing|annotating|ready|failed
    variant_count INTEGER,
    create_time   TIMESTAMPTZ NOT NULL DEFAULT now(),
    update_time   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2026-09-21: the websocket upload manager starts processing once per received file, so the
-- same VCF can reach the handler twice; the ingest skips a hash it has already made ready.
ALTER TABLE th_sequencing_sample ADD COLUMN IF NOT EXISTS content_sha256 VARCHAR(64);
-- storage key of the raw VCF (th_files.file_key): the trio backfill re-reads a relative's file by it
ALTER TABLE th_sequencing_sample ADD COLUMN IF NOT EXISTS file_key VARCHAR(255);
CREATE INDEX IF NOT EXISTS idx_th_sequencing_sample_hash ON th_sequencing_sample (user_id, content_sha256);

CREATE TABLE IF NOT EXISTS th_variant (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sample_id   INTEGER NOT NULL REFERENCES th_sequencing_sample(id),
    user_id     VARCHAR(200) NOT NULL,
    chrom       VARCHAR(8)  NOT NULL,
    pos         INTEGER     NOT NULL,
    ref         TEXT        NOT NULL,
    alt         TEXT        NOT NULL,
    genotype    VARCHAR(8),
    zygosity    VARCHAR(16),                       -- het | hom | hemi
    depth       INTEGER,
    gq          INTEGER,
    filter      VARCHAR(64),
    gene_symbol VARCHAR(64),
    hgvs_c      TEXT,
    hgvs_p      TEXT,
    consequence VARCHAR(64),
    is_de_novo  BOOLEAN,                           -- NULL = unknown (no parents), not false
    inheritance VARCHAR(16),                       -- de_novo | maternal | paternal | biparental | unknown
    create_time TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_th_variant_call UNIQUE (sample_id, chrom, pos, ref, alt)
);
CREATE INDEX IF NOT EXISTS idx_th_variant_user_gene ON th_variant (user_id, gene_symbol);
CREATE INDEX IF NOT EXISTS idx_th_variant_locus     ON th_variant (chrom, pos);

CREATE TABLE IF NOT EXISTS th_variant_annotation (
    id                    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    variant_id            BIGINT NOT NULL REFERENCES th_variant(id),
    source                VARCHAR(32) NOT NULL,     -- clinvar | gnomad | manual
    source_version        VARCHAR(32) NOT NULL,
    clinical_significance VARCHAR(64),
    review_status         VARCHAR(64),
    condition_names       TEXT[],
    af_global             DOUBLE PRECISION,
    af_popmax             DOUBLE PRECISION,
    popmax_pop            VARCHAR(16),
    allele_count          INTEGER,
    acmg_class            VARCHAR(8),
    acmg_codes            TEXT[],
    asserted_by           VARCHAR(200),
    note                  TEXT,
    create_time           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_th_variant_annotation UNIQUE (variant_id, source, source_version)
);
