-- Who created each project (016): its creator may change its visibility (D118).

ALTER TABLE projects ADD COLUMN created_by bigint REFERENCES users ON DELETE SET NULL;

-- Projects made before this were mostly created by a run's upload (`wandb.init`), and nothing
-- recorded who asked: their creator is whoever logged their earliest run. Projects without an
-- owned run keep no creator, so only admins change their visibility.
UPDATE projects p SET created_by = (
    SELECT r.owner_pk FROM runs r
    WHERE r.project_pk = p.pk AND r.owner_pk IS NOT NULL
    ORDER BY r.created_at, r.pk
    LIMIT 1
);
