-- The dashboard's Storage tab lives at `/storage/{org}`, so `storage` joins the names an org
-- can't have (the dashboard's own top-level pages). An org that already has it would be
-- unreachable there: refuse to migrate until it's renamed, rather than hide it.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM orgs WHERE lower(name) = 'storage') THEN
        RAISE EXCEPTION 'an organization is named "storage", which the dashboard now uses for '
            'its Storage tab: rename it (UPDATE orgs SET name = ... WHERE name = ...) and start '
            'Flock again';
    END IF;
END
$$;
