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

CREATE TABLE IF NOT EXISTS th_consent (
    id          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     VARCHAR(200) NOT NULL,
    scope       VARCHAR(32)  NOT NULL,             -- own_care | family_analysis | research
    purpose     TEXT,
    granted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at  TIMESTAMPTZ,
    granted_by  VARCHAR(200),
    create_time TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_th_consent_user ON th_consent (user_id) WHERE revoked_at IS NULL;
