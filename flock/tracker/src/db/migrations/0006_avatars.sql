-- Avatar images of users and organizations, stored by the SHA-256 of their bytes: the digest
-- is the public URL (/api/v1/avatars/<hex>), so an image's URL changes whenever it does.

CREATE TABLE avatars (
    digest bytea PRIMARY KEY,
    content_type text NOT NULL,
    data bytea NOT NULL,
    created_at timestamptz NOT NULL
);

ALTER TABLE users ADD COLUMN avatar bytea REFERENCES avatars;
-- Where the user's avatar came from: fetched from Gravatar, or uploaded.
ALTER TABLE users ADD COLUMN avatar_source text CHECK (avatar_source IN ('gravatar', 'upload'));
ALTER TABLE orgs ADD COLUMN avatar bytea REFERENCES avatars;
