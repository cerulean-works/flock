-- Selecting many datasets or models (019).

-- A deleted folder is one trash entry, everything under its prefix, restored as a whole (D132).
ALTER TABLE dataset_trash ADD COLUMN kind text NOT NULL DEFAULT 'dataset'
    CHECK (kind IN ('dataset', 'folder'));

-- A model's folder in flock-models stops following its name (D134): uploads go to
-- `{prefix}/u/{ulid}/`, and renaming a model changes only `name`. Every model so far is in the
-- folder of its name.
ALTER TABLE models ADD COLUMN prefix text;
UPDATE models SET prefix = name;
ALTER TABLE models ALTER COLUMN prefix SET NOT NULL;
