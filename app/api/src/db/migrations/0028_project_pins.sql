-- Projects a user pinned to the top of their sidebar, oldest pin first (stage 260 quality of
-- life). Each user's own: pinning changes nothing for anyone else.
CREATE TABLE project_pins (
    user_pk bigint NOT NULL REFERENCES users ON DELETE CASCADE,
    project_pk bigint NOT NULL REFERENCES projects ON DELETE CASCADE,
    pinned_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_pk, project_pk)
);
