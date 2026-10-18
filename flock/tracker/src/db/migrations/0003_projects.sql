-- Projects become records: runs reference them by key, so renaming a project or moving a run
-- is a one-row update, and deleting a project cascades to its runs.

CREATE TABLE projects (
    pk bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity text NOT NULL,
    name text NOT NULL,
    description text,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    UNIQUE (entity, name)
);

INSERT INTO projects (entity, name, created_at, updated_at)
SELECT entity, project, min(created_at), max(updated_at)
FROM runs
GROUP BY entity, project;

ALTER TABLE runs ADD COLUMN project_pk bigint REFERENCES projects ON DELETE CASCADE;
UPDATE runs SET project_pk = p.pk
FROM projects p
WHERE p.entity = runs.entity AND p.name = runs.project;
ALTER TABLE runs ALTER COLUMN project_pk SET NOT NULL;
ALTER TABLE runs DROP CONSTRAINT runs_entity_project_name_key;
ALTER TABLE runs ADD CONSTRAINT runs_project_pk_name_key UNIQUE (project_pk, name);
ALTER TABLE runs DROP COLUMN entity, DROP COLUMN project;
