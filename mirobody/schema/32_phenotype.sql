-- Layer 1 of the rare-disease MVP (docs/rare-mvp-plan.md §3.2): one HPO assertion per row.
-- Append-only, replayable. The plan named this file `a6_phenotype.sql`; the 1.5.0 schema
-- moved to two-digit prefixes (README), so it lands as 32_ beside the observation tables.
--
-- `hpo_label` is a snapshot (HPO renames terms), `negated` is first-class (an excluded
-- feature is evidence, not a missing row), `source` separates clinician from nlp.
CREATE TABLE IF NOT EXISTS th_phenotype (
    id               INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id          VARCHAR(200) NOT NULL,
    hpo_id           VARCHAR(16)  NOT NULL,        -- 'HP:0001250'
    hpo_label        TEXT         NOT NULL,        -- label at write time
    onset_hpo_id     VARCHAR(16),                  -- HP:0003593 Infantile onset ...
    severity_hpo_id  VARCHAR(16),                  -- HP:0012825 Mild / Moderate / Severe
    frequency_hpo_id VARCHAR(16),                  -- HP:0040280 Obligate ... HP:0040285 Excluded
    negated          BOOLEAN NOT NULL DEFAULT false,
    subject          VARCHAR(16) NOT NULL DEFAULT 'proband',  -- proband | father | mother | sibling | other_relative
    source           VARCHAR(32) NOT NULL,         -- clinician | nlp | import
    source_text      TEXT,                         -- the free text the NLP mapping came from
    confidence       REAL,                         -- NLP score; NULL for clinician
    asserted_at      TIMESTAMPTZ,                  -- when the finding was recorded (≠ insert time)
    file_id          BIGINT,                       -- provenance → th_files
    deleted          BOOLEAN NOT NULL DEFAULT false,
    create_time      TIMESTAMPTZ NOT NULL DEFAULT now(),
    update_time      TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 2026-09-21: the finer subject axis of plan §14.2 (father | mother | sibling | other_relative), so a
-- family-history query can hang a narrative assertion on the right pedigree member
ALTER TABLE th_phenotype ADD COLUMN IF NOT EXISTS subject_role VARCHAR(16);
CREATE INDEX IF NOT EXISTS idx_th_phenotype_user ON th_phenotype (user_id) WHERE NOT deleted;
CREATE INDEX IF NOT EXISTS idx_th_phenotype_hpo  ON th_phenotype (hpo_id)  WHERE NOT deleted;

-- Disease-level coding (plan D8): the ORPHA / OMIM code a case was assigned, with provenance.
CREATE TABLE IF NOT EXISTS th_disease_code (
    id               INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id          VARCHAR(200) NOT NULL,
    system           VARCHAR(16)  NOT NULL,        -- ORPHA | OMIM | ICD-10
    code             VARCHAR(32)  NOT NULL,
    label            TEXT         NOT NULL,
    status           VARCHAR(16)  NOT NULL DEFAULT 'candidate',  -- candidate | confirmed | excluded
    source           VARCHAR(32)  NOT NULL,        -- clinician | nlp | import
    source_text      TEXT,
    confidence       REAL,
    file_id          BIGINT,
    deleted          BOOLEAN NOT NULL DEFAULT false,
    create_time      TIMESTAMPTZ NOT NULL DEFAULT now(),
    update_time      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_th_disease_code_user ON th_disease_code (user_id) WHERE NOT deleted;
