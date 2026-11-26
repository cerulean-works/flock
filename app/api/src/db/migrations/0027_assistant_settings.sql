-- The run groups assistant's proposal model and speed per org (design doc 026, D230). A null
-- model uses the server's FLOCK_OPENAI_PROPOSAL_MODEL; the tier is OpenAI's service tier.
ALTER TABLE orgs ADD COLUMN assistant_model text,
    ADD COLUMN assistant_tier text NOT NULL DEFAULT 'standard'
        CHECK (assistant_tier IN ('standard', 'fast', 'ultrafast'));
