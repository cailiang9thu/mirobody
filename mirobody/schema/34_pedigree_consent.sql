-- Layer 3 + consent of the rare-disease MVP (docs/rare-mvp-plan.md §6.2 / §7.1).
-- Biological relations, not `care_circles` authorization: `analysis_only` is the
-- "computed with, never reported on" semantics the access levels cannot express.
CREATE TABLE IF NOT EXISTS th_pedigree (
    id          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    family_id   VARCHAR(64) UNIQUE NOT NULL,
    label       VARCHAR(256),
    create_time TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS th_pedigree_member (
    id            INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pedigree_id   INTEGER NOT NULL REFERENCES th_pedigree(id),
    user_id       VARCHAR(200),                    -- NULL: a relative without an account
    individual_id VARCHAR(64) NOT NULL,
    paternal_id   VARCHAR(64),
    maternal_id   VARCHAR(64),
    sex           SMALLINT,                        -- 1 male, 2 female, NULL unknown
    affected      SMALLINT,                        -- 1 no, 2 yes, NULL unknown
    is_proband    BOOLEAN NOT NULL DEFAULT false,
    analysis_only BOOLEAN NOT NULL DEFAULT false,
    create_time   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_pedigree_individual UNIQUE (pedigree_id, individual_id)
);

-- Three-level consent (plan §7.1): each scope is a separate row, never bundled; a minor's
-- row records the guardian who signed. `permit()` in mirobody_rare/consent/gate.py is the
-- only reader.
CREATE TABLE IF NOT EXISTS th_consent (
    id                   INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id              VARCHAR(200) NOT NULL,
    scope                VARCHAR(24)  NOT NULL,   -- individual_return | research_use | commercial_use
    granted              BOOLEAN      NOT NULL,
    signed_by_user_id    VARCHAR(200) NOT NULL,
    relationship         VARCHAR(24),             -- self | guardian
    layer                VARCHAR(16),             -- phenotype | variant | signal | pedigree; NULL = all
    residency            VARCHAR(8),              -- CN | US | EU
    cross_border_allowed BOOLEAN NOT NULL DEFAULT false,
    effective_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at           TIMESTAMPTZ,
    document_file_id     BIGINT,
    CONSTRAINT uq_th_consent_live UNIQUE (user_id, scope, layer, effective_at)
);
CREATE INDEX IF NOT EXISTS idx_th_consent_user ON th_consent (user_id) WHERE revoked_at IS NULL;
