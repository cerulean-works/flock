# 005: avatars for users and organizations

| | |
| --- | --- |
| Status | applied |
| Date | 2026-10-18 |
| Builds on | [003](003-organizations-and-auth.md), [004](004-home-and-budgets.md) |
| Keeps | D2 (one server per database, small state in memory), D18–D29 |
| Result | 208 tests in `tests/tracker/` pass; see [section 4](#4-how-it-was-actually-built) |

## 1. Goal

flock-ui shows people and organizations as pictures: in the feed, member lists, the budget
panel's top runs, and the account menu. This stage gives every user and organization an
avatar image that the tracker stores and serves.

1. **Gravatar on account creation.** When an account is created (the first signup, an
   accepted invitation, or a Google sign-in that creates one), the tracker fetches the email's
   Gravatar in the background. If one exists, it becomes the account's avatar.
2. **Uploads.** Users set their own avatar. Org admins set their organization's. Either can be
   removed, and a user can re-fetch from Gravatar.
3. **Serving.** Every response that shows a user or an organization carries an `avatar_url`.
   The image behind it loads in a plain `<img>`, with no credentials.

**Out of scope:**
- profile pages and profile fields beyond what exists (name, email);
- Gravatar profile data (display name, bio and so on);
- resizing or re-encoding images;
- SVG avatars.

---

## 2. Design decisions

The numbering continues from 004.

### D30. Avatars are stored in the tracker, content-addressed

Migration `0006_avatars.sql` adds three things:
- `avatars(digest bytea PRIMARY KEY, content_type, data bytea, created_at)`, where `digest` is
  the SHA-256 of the image bytes;
- `users.avatar` and `orgs.avatar`, which reference `avatars(digest)` and are NULL when there
  is no avatar;
- `users.avatar_source`, which is `gravatar` or `upload`.

**Why store rather than link to gravatar.com:**
- avatars work where Gravatar is blocked or offline;
- viewers' browsers never contact a third party;
- one endpoint serves both Gravatar images and uploads.

**Why content-addressed.** The URL is `/api/v1/avatars/<hex digest>`, and it is served without
credentials:
- a browser `<img>` can't send the `Authorization` header that every other endpoint needs;
- the digest is unguessable, so knowing the URL is the permission, the same as a run file's
  signed upload URL;
- the bytes behind a URL never change, so responses are
  `Cache-Control: public, max-age=31536000, immutable` with the digest as the ETag;
- changing an avatar changes its URL, so caches never show a stale picture.

**Sharing and cleanup.** Identical images share one row. When the last user or organization
pointing at a digest moves off it, or is deleted, the row is deleted, so old URLs answer 404.

**In memory.** `User.avatar`, `User.avatar_source` and `Org.avatar` hold the digests, so adding
`avatar_url` to responses costs nothing. The image bytes are read from PostgreSQL per request.
Browsers cache them for a year, so few requests reach the tracker.

### D31. What counts as an image

The tracker checks an image's leading bytes and ignores the `Content-Type` the client sends.

| Type | Leading bytes |
| --- | --- |
| PNG | `89 50 4E 47 0D 0A 1A 0A` |
| JPEG | `FF D8 FF` |
| GIF | `GIF87a` or `GIF89a` |
| WebP | `RIFF`, 4 bytes, then `WEBP` |

- **Rejected:** anything else answers 415. SVG is refused because it can carry scripts.
- **Size:** at most 1 MiB, enforced while the body is read; larger answers 413. An empty body
  answers 422.
- **Serving:** images go out with their detected type, plus `X-Content-Type-Options: nosniff`
  and `Content-Security-Policy: default-src 'none'`.
- **No resizing:** that would add an imaging dependency, and the dashboard scales the images.

### D32. Gravatar

- **Request:** `GET <gravatar_url><sha256(lowercased email)>?s=256&d=404`. The new setting
  `TRACKER_GRAVATAR_URL` defaults to `https://gravatar.com/avatar/`; a Libravatar-compatible
  mirror also works. `d=404` makes "no Gravatar" a 404 instead of a generated picture, so
  accounts without one have no avatar.
- **Off switch:** the new setting `TRACKER_GRAVATAR` (default `true`). The request reveals the
  hash of the email to gravatar.com, so a deployment can turn it off.
- **When:** after the account is created and its transaction has committed, in a background
  task, so creating an account never waits on gravatar.com. The fetch uses `urllib` in a
  thread with a 10 s timeout. Failures, non-images and oversized responses are logged and
  ignored.
- **No overwrite:** an avatar set while the fetch was running is kept.
- **Refetch:** `POST /me/avatar/gravatar` fetches again on demand, which also covers accounts
  created before this stage. It answers 404 when Gravatar has no image, and 409 when Gravatar
  is turned off.

### D33. The API

All paths are under `/api/v1`.

| Endpoint | Who | Effect |
| --- | --- | --- |
| `GET /avatars/{digest}` | anyone, no credentials | The image. Unknown digests answer 404. `If-None-Match` answers 304. |
| `PUT /me/avatar` | signed-in user | The body is the image bytes. Sets `avatar_source: upload`. Returns `/me`. |
| `DELETE /me/avatar` | signed-in user | Removes the avatar. Returns `/me`. |
| `POST /me/avatar/gravatar` | signed-in user | Fetches from Gravatar now. Returns `/me`. |
| `PUT /orgs/{org}/avatar` | org admins | Sets the organization's image. Returns the organization. |
| `DELETE /orgs/{org}/avatar` | org admins | Removes it. Returns the organization. |

`avatar_url` (a path such as `/api/v1/avatars/3f…`, or `null`) is added to:
- user objects: `/me`, `/users`, sign-in responses, and organization members;
- `actor` and `owner` in the feed and the budget panel;
- organization objects, and the organization of a public invitation (`org_avatar_url`).

`/me` also returns `avatar_source`.

The URL is a path on the tracker. A page served from another origin either prefixes the
tracker's address, or forwards `/api/v1/avatars/*` to the tracker so the path works unchanged.
flock-ui's own API lives under `/api/`, and today it forwards only `/api/v1/auth/google/*`
to the tracker, so its avatar support needs a `/api/v1/avatars/*` pass-through too.

### D34. Known gaps

- Images aren't resized or re-encoded, so a 1 MiB image is served at 1 MiB.
- Animated GIFs stay animated.
- Super admins can't set another user's avatar. They can delete the account.
- Gravatar is consulted only at account creation and on request, so a later Gravatar change
  doesn't propagate.
- Anyone who has an avatar URL can load it, even after leaving the organization, until the
  avatar changes.

---

## 3. Plan

1. **Migration 0006**, with a test.
2. **`src/core/avatars.py`:** `Avatars` (`store.avatars`), with type detection, `set_user`,
   `set_org`, `image`, `gravatar` and the background fetch. Loading digests is part of the
   account and org loads.
3. **Hooks:**
   - `signup` and `register` schedule the fetch;
   - `delete_user` and `delete_org` release the avatar.
4. **Settings:** `gravatar`, `gravatar_url`. Tests default to `gravatar=False`, with a fake
   Gravatar server where it's needed.
5. **API:** the routes in D33 (new `src/api/avatars.py`), and `avatar_url` in the responses
   listed there.
6. **Tests:** `test_avatars.py` covers Gravatar on signup, invite and Google accounts; no
   Gravatar; uploads and validation; public serving and caching; org avatars and permissions;
   cleanup; and persistence across a restart.
7. **Docs:** README, the docs site (accounts guide, API reference, configuration), and this doc
   marked `applied`.

---

## 4. How it was actually built

The plan held. These are the details it left open.

- **Modules.** `src/core/avatars.py` has `image_type`, `ImageError` (which carries the HTTP
  status: 413, 415 or 422) and `Avatars` (`store.avatars`). `src/api/avatars.py` has the
  routes. It reads upload bodies as a stream and stops at 1 MiB, so a large upload is refused
  without being buffered. It checks `Content-Length` first.
- **One shape for a user shown next to something.** The new `Accounts.brief(user)` returns
  `{id, name, email, avatar_url}`. Feed actors, budget owners (through `Budgets.owner`) and
  member lists use it. Members keep their `role`.
- **Changes are serialized** by one `asyncio.Lock` in `Avatars`. Each change is one
  transaction:
  1. insert the image (`ON CONFLICT DO NOTHING`, so identical images share a row);
  2. point the user or org at it;
  3. delete the old digest unless another user or org still uses it.

  `delete_user` and `delete_org` release the digest after their own delete commits.
- **The background fetch** runs only after the account's transaction has committed, from
  `signup` and `register`, which also covers Google sign-ins that create accounts.
  - It skips a user who has an avatar by the time the download finishes, and a user deleted
    meanwhile.
  - It logs network, HTTP and image errors as warnings.
  - `Store.close` waits for pending fetches (`Avatars.flush`), and tests call the same method
    to wait for them.
- **On-demand fetch errors.** `POST /me/avatar/gravatar` answers 502 when Gravatar can't be
  reached or sends something that isn't an image.
- **Tests.** `TrackerServer` defaults to `gravatar=False`, so no test reaches gravatar.com.
  `test_avatars.py` runs a fake Gravatar (`FakeGravatar`, which serves by email hash and
  answers 404 otherwise) through `gravatar_url`. It covers:
  - new accounts, and the request's `s=256&d=404`;
  - public serving, caching headers and 304;
  - uploads, every refusal, sharing and cleanup;
  - org avatars, permissions and the invitation's `org_avatar_url`;
  - on-demand fetch, including 409 and 502;
  - an upload winning over a late Gravatar;
  - a restart.

  Migration 0006 only adds tables and nullable columns. The existing upgrade tests apply it to
  populated databases and start a tracker on them.
