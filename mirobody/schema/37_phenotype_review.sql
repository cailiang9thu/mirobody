-- Human-confirmation queue for the rare-disease phenotype coder (docs/ingest-plan.md §2.4).
-- An ambiguous or uncoded assertion is never written to th_phenotype as 'nlp'; it waits here
-- until `resolve_phenotype_review` turns it into a 'clinician' row. Append-only, replayable.
CREATE TABLE IF NOT EXISTS th_phenotype_review (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id       VARCHAR(200) NOT NULL,
    file_id       BIGINT,
    kind          VARCHAR(16)  NOT NULL,        -- ambiguous | abstained | figure | truncated
    source_text   TEXT         NOT NULL,
    candidates    TEXT[],
    section       VARCHAR(64),
    subject       VARCHAR(16),
    negated       BOOLEAN,
    resolved_hpo  VARCHAR(16),
    resolved_by   VARCHAR(200),
    resolved_at   TIMESTAMPTZ,
    create_time   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_th_phenotype_review_open ON th_phenotype_review (user_id) WHERE resolved_at IS NULL;
ALTER TABLE th_phenotype ADD COLUMN IF NOT EXISTS section VARCHAR(64);
