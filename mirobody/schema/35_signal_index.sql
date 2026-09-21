-- Layer 4 of the rare-disease MVP (docs/rare-mvp-plan.md §5.2): raw signal objects (DICOM / EDF)
-- stored as-is in object storage, indexed here by DE-IDENTIFIED metadata only. Plan name: a8_.
-- An object whose deid_status is not 'done' is invisible to the query tools (§5.3).
CREATE TABLE IF NOT EXISTS th_signal_object (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id        VARCHAR(200) NOT NULL,
    file_id        BIGINT NOT NULL,                  -- th_files: the raw object + storage key
    modality       VARCHAR(16) NOT NULL,             -- MRI | CT | PET | XR | US | RTSTRUCT | SEG | EEG | WSI | OTHER
    format         VARCHAR(16) NOT NULL,             -- DICOM | EDF | DICOM-WSI
    body_part      VARCHAR(64),
    study_date     DATE,
    series_desc    TEXT,
    instance_count INTEGER,
    duration_sec   INTEGER,                          -- EEG
    channel_count  INTEGER,                          -- EEG
    report_summary TEXT,
    report_file_id BIGINT,
    residency      VARCHAR(8) NOT NULL DEFAULT 'CN', -- CN | US | EU
    exportable     BOOLEAN NOT NULL DEFAULT false,
    deid_status    VARCHAR(16) NOT NULL DEFAULT 'pending',  -- pending | done | failed
    create_time    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_th_signal_user_modality ON th_signal_object (user_id, modality);
