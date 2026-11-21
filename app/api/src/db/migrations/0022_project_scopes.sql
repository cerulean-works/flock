-- Model folders and project scopes (022): models, dataset trash entries and Hugging Face imports
-- get a project (null: global). Projects' datasets live in flock-datasets under
-- -projects/{pk}/, which needs no schema.

-- A model's scope (D159). The old unique index on (org_pk, name) stays until
-- 0023_models_scope.contract.sql, so the release before this one keeps its names apart (D170).
ALTER TABLE models ADD COLUMN project_pk bigint REFERENCES projects ON DELETE CASCADE;
CREATE UNIQUE INDEX models_global_name ON models (org_pk, name)
    WHERE project_pk IS NULL AND deleted_at IS NULL;
CREATE UNIQUE INDEX models_project_name ON models (project_pk, name)
    WHERE project_pk IS NOT NULL AND deleted_at IS NULL;
-- Folder listings and the checks that a path isn't a folder or under a model (D158).
CREATE INDEX models_folders ON models (org_pk, project_pk, name text_pattern_ops);

ALTER TABLE dataset_trash ADD COLUMN project_pk bigint REFERENCES projects ON DELETE CASCADE;
ALTER TABLE hf_imports ADD COLUMN project_pk bigint REFERENCES projects ON DELETE CASCADE;

-- The S3 proxy lets writes under -projects/{pk}/ of flock-datasets only into a live project of
-- the org (D160): it drops its cache when projects come and go.
CREATE TRIGGER projects_s3_invalidate AFTER INSERT OR UPDATE OF deleted_at OR DELETE ON projects
    FOR EACH STATEMENT EXECUTE FUNCTION s3_invalidate();
