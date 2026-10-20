-- Organizations get a display name: what people see, free to change. `name` becomes the
-- organization's ID (URLs, API paths, the wandb SDK's entity) and no longer changes. Existing
-- organizations are shown as they were named.

ALTER TABLE orgs ADD COLUMN display_name text;
UPDATE orgs SET display_name = name;
ALTER TABLE orgs ALTER COLUMN display_name SET NOT NULL;
