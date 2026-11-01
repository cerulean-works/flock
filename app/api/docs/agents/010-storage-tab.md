# 010: a Storage tab, with a bucket overview and a file browser

| | |
| --- | --- |
| Status | applied |
| Date | 2026-10-27 |
| Builds on | [009](009-s3-proxy.md) |
| Amends | D53 (bucket management moves out of org settings), D60 (where the buckets UI lives), D61 (presigned browser requests and CORS) |
| Keeps | D53–D59, D62 |
| Result | 382 tests in `tests/app`, `tests/cli` and `tests/s3` pass on moto (10 new in `tests/app/test_s3_objects.py`, 5 in `tests/s3/test_browser.py`, 1 in `tests/app/test_migrations.py`), and the 100 S3-backed ones also pass against the real Ceph; 359 in `app/web` (32 new); see [section 6](#6-how-it-was-actually-built) |

## 1. Goal

009 put buckets and key lifetimes in the org settings page, and S3 keys in the account page.
That's admin territory, while buckets are everyday working storage. This stage gives storage a
place of its own in the sidebar, like Cloudflare R2's dashboard:

1. **A "Storage" item** in the sidebar, under Runs, for the current org.
2. **A bucket overview**: the org's buckets, creating and deleting them, and how to connect.
3. **A file browser** inside each bucket: folders and objects, previews, uploads, downloads
   and deletes.
4. **Buckets move here.** The buckets section leaves the org settings page. Org settings,
   including the S3 key lifetimes, stay where they are (D68).

**Out of scope:**
- renaming or moving objects (S3 has no rename; a copy and delete would be a later feature);
- bucket sizes and object counts (D66 says why);
- object versions, lifecycle rules, public buckets and custom domains;
- search across a bucket beyond prefix filtering;
- editing objects in place.

---

## 2. Design decisions

The numbering continues from 009.

### D63. Where the Storage tab lives

- **The sidebar** gets "Storage" (lucide `hard-drive`) after Runs, shown when S3 is on
  (`auth/config.s3`) and an org is in view. It's also in the collapsed rail.
- **Routes**, under `(app)`:

  | URL | Page |
  | --- | --- |
  | `/storage/{org}` | bucket overview (D64) |
  | `/storage/{org}/{bucket}` | the bucket's root (D65) |
  | `/storage/{org}/{bucket}/{path…}/` | a folder: a path ending in `/` |
  | `/storage/{org}/{bucket}/{path…}` | an object's details and preview (D67) |

- **Why under `/storage/`.** Orgs are `/{org}` and projects `/{org}/{project}`, so
  `/{org}/storage` would collide with a project named `storage`. A top-level segment avoids
  that. `storage` joins `RESERVED` in `src/lib/utils.ts`, so no org can take the name. The
  migration refuses to run if an org named `storage` already exists, and says so.
- **Breadcrumbs** at the top of every page: Storage › org › bucket › folders. Each one is a
  link.
- **Switching orgs** in the sidebar while on a storage page goes to that org's
  `/storage/{org}`.

### D64. The bucket overview

`/storage/{org}` is a table of the org's buckets, with name, creation time and creator. Each
row opens the bucket. Above the table:

- **Create bucket.** A name field that checks the 57-character rule (`name_limit`) as you type.
  This is the same create form as 009's org page, moved here.
- **Connect.** A panel with the proxy's endpoint and region, and three ready-to-copy snippets:
  - `flock s3 configure` with `aws s3 ls --profile flock`;
  - `eval "$(flock s3 credentials)"`;
  - a `flock.cli.s3` Python snippet.

  It links to the account page to create a long-lived key.

Each row's menu has:
- **History**: the bucket's events (created and deleted, by whom and through what), in a
  side sheet;
- **Delete**: admins only, using the type-the-name dialog from 009.

A bucket still being purged after a delete is shown greyed with "being deleted" until the purge
finishes, so a name that can't be reused yet explains itself.

### D65. The file browser

`/storage/{org}/{bucket}/{prefix}` lists one level of the bucket, like R2:

- **The listing:** folders first (common prefixes with delimiter `/`), then objects. Columns are
  name, size, type (from the extension), and last modified. Folders show `—` for size and date.
- **Paging:** 200 entries per page, with "Load more" driven by S3's continuation token. The URL
  keeps the prefix, not the page.
- **Filtering:** a box that filters by name prefix within the current folder, as a `prefix`
  query to S3, not in the browser.
- **Selecting:** checkboxes select objects, with shift-click for ranges. The bar then offers
  Download (one object) and Delete.
- **Actions in the header:**
  - **Upload:** a file picker, also drag and drop onto the list. Folders dropped keep their
    relative paths. Each file shows a progress row, and the listing refreshes when they finish.
  - **Create folder:** writes an empty object named `prefix/name/`, which is how S3 consoles,
    R2 included, make folders appear before anything is in them.
  - **Copy S3 URI** of the current folder: `s3://{bucket}/{prefix}`.
- **Deleting:**
  - Deleting objects is a confirmation dialog listing them.
  - Deleting a folder deletes everything under its prefix. The API lists and deletes in batches
    of 1000. Up to 10,000 objects this runs in the request. Above that, the API refuses with
    the count and the CLI command to use instead (`aws s3 rm --recursive`), so a slip in the UI
    can't silently empty a huge prefix.
- **Permissions.** Members and admins both read, write and delete objects, as D54 says. The browser
  always acts with a read-write key, but the API checks membership on every call.

### D66. No bucket sizes

Ceph reports a bucket's size and object count only through its Admin Ops API, which the global key
can't use (009, D53). Counting by listing would mean reading every key in the bucket on each
visit. So the overview shows no sizes, and a folder's size isn't summed. Object sizes, from the
listing, are shown. If the key gains admin rights later, sizes can come from
`/admin/bucket?stats`.

### D67. Object details and previews

Opening an object shows a page with a preview and a details panel.

- **Details:** key, size, last modified, ETag, content type and user metadata (`x-amz-meta-*`),
  from a HEAD.
- **Actions:** Download, Copy S3 URI, Copy a presigned link valid for 1 hour, and Delete.
- **Previews,** by content type, then by extension:

  | Kind | Preview |
  | --- | --- |
  | images (`png`, `jpg`, `gif`, `webp`, `svg`) | shown inline; SVG in an `<img>`, never inlined, so its scripts don't run |
  | text (`txt`, `log`, `md`, `py`, `yaml`, `toml`, `csv`, `jsonl`, …) | the first 1 MiB, by a Range request, in a monospace block; Markdown rendered with `marked` + DOMPurify |
  | `json` | pretty-printed, if the first 1 MiB parses |
  | video and audio | the browser's player, streaming with ranges |
  | `pdf` | the browser's viewer in an iframe |
  | anything else | no preview, only the details and Download |

- **Truncation.** A text preview cut at 1 MiB says so, and links the full download.

### D68. Org settings stay in org settings

Key lifetimes (D56) are an org setting, so their section stays on the org settings page, where
009 put them. Only bucket management moves to Storage. The org settings page loses its Buckets
section and gets a one-line link in its place: "Buckets moved to Storage". Personal S3 keys stay
on the account page, since they belong to the user rather than the org. The Connect panel (D64)
links there.

### D69. How the browser reaches objects

Listing and deleting go through the API. Object bytes go straight between the browser and the
S3 proxy, by presigned URL, so large files never pass through the web app or the API.

- **New API routes** under `orgs/{org}/buckets/{bucket}`. They check membership and act upstream
  with the global key, like 009's bucket routes:

  | Route | Does |
  | --- | --- |
  | `GET objects?prefix&cursor&limit` | one level of listing: `{folders, objects: [{key, size, last_modified, etag}], cursor}` |
  | `GET object?key` | HEAD: the details of D67 |
  | `POST objects/delete {keys, prefixes}` | deletes objects, and everything under each prefix (the 10,000 cap of D65) |
  | `POST folders {prefix}` | creates the empty folder marker |
  | `POST presign {method, key, …}` | presigned URLs for the browser (below) |

- **Presigned URLs.** The proxy already verifies presigned SigV4 queries (009's `sigv4.py`).
  `POST presign` signs URLs to the proxy with a **browser key**: an ephemeral key (D57) issued for
  the request's browser session and org, named `browser (ephemeral)`.
  - **One key per session and org.** It's reused while it has more than 15 minutes left, so
    browsing doesn't create a key per click. It's deleted with the session (logout, password
    change), like any key issued for a session.
  - **URL lifetimes:** 15 minutes for uploads and previews, and 1 hour for "Copy presigned link".
  - **GET URLs** set `response-content-disposition` to `attachment` for downloads, and to
    `inline` for previews.
- **Uploads.**
  - **Up to 100 MiB:** one presigned PUT.
  - **Larger files:** S3 multipart, done by the browser. `presign` with `method: multipart`
    creates the upload and returns presigned URLs for its parts (16 MiB each, four at a time),
    plus one for CompleteMultipartUpload. A cancelled or failed upload is aborted through the
    same route.
  - **The limit:** 50 GiB per file through the UI. Larger files are for the CLI.
- **CORS on the proxy.** Browsers send requests to the proxy cross-origin, so the proxy answers
  CORS:
  - It answers `OPTIONS` preflights without authentication, for the origin in `FLOCK_WEB_URL`
    only.
  - It allows `GET`, `HEAD`, `PUT`, `POST` and `DELETE` and the headers S3 uses, and exposes
    `ETag`, which multipart completion needs.
  - Other origins get no CORS headers, so presigned URLs can't be used from other sites'
    pages. They still work with `curl`, which is the point of "Copy presigned link".
- **Reaching the proxy.** The browser must be able to reach `FLOCK_S3_PUBLIC_URL`, as S3
  clients already must. If it can't, previews, uploads and downloads fail with a message naming
  the URL, and listing still works.

---

## 3. Plan

One PR, `feat/storage-tab`, stacked on `feat/s3-proxy` (#34).

### Step 1. API

The object routes and `presign` of D69. The browser key goes in `core/s3.py`: issue or reuse
it for a session and org. Folder deletion is capped as D65 says.

### Step 2. Proxy

CORS per D69. Check presigned PUT, multipart and GET with `response-content-disposition`
against moto and Ceph.

### Step 3. Web

- The sidebar item and `RESERVED`.
- The routes of D63.
- The overview, moving `OrgBuckets` and `DeleteBucketDialog` out of the org page, which keeps
  `S3Lifetimes` and a link to Storage.
- The file browser, with its selection, upload queue (single and multipart, with progress and
  cancel) and folder creation.
- The object page and its previews.
- Types, and specs for each page.

### Step 4. Migration

`0012_reserve_storage.sql` refuses to run when an org named `storage` exists (D63), and adds
nothing else.

### Step 5. Tests

- `tests/app/test_s3_objects.py`:
  - listing (folders, paging, prefix filter);
  - HEAD;
  - deleting objects and prefixes, and the 10,000 cap;
  - folder markers;
  - membership checks;
  - presign: the browser key reused, rotated near expiry and deleted with the session.
- `tests/s3/test_browser.py`:
  - presigned PUT, GET (inline and attachment) and a full browser-style multipart upload
    through the proxy;
  - CORS preflight for the web origin, and none for another origin.
- Web specs: the listing and paging, selection, the upload queue, the delete dialogs, the
  previews per type (with a malicious SVG shown only as an image), and breadcrumbs.
- **Manual:** browse, upload a file over 100 MiB, preview an image, a text file and a PDF,
  delete a folder, all against Ceph with a test prefix.

### Step 6. Docs and status

- An API docs page for the object routes.
- A "Storage" page for the web app's docs, if one exists for the dashboard.
- Mark this file `applied`, with a "How it was actually built" section.

---

## 4. Known gaps after this stage

- **No sizes** for buckets and folders (D66).
- **No rename or move** of objects.
- **Folder deletes above 10,000 objects** need the CLI.
- **Previews read at most 1 MiB of text**, and nothing of formats such as Parquet or Lance.

## 5. Settled in review

1. **Personal S3 keys** stay on the account page, linked from the Storage tab (D68).
2. **Object bytes go straight between the browser and the S3 proxy** by presigned URL, with CORS
   on the proxy (D69).
3. **Org settings**, including the S3 key lifetimes, stay on the org settings page (D68).

## 6. How it was actually built

The plan was followed, in five commits: the proxy's CORS and presigning (step 2), the API, the
migration and their tests (steps 1, 4 and 5), the web app (step 3), the compose files, and the
docs (step 6). These are the places where the code differs from the decisions above, or adds to
them.

- **Presigning in batches.** D69 has `multipart` return the URLs of every part and of the
  completion. URLs live 15 minutes and a 50 GiB upload takes longer, so `multipart` only starts
  the upload and returns `upload_id`, `part_size` (16 MiB) and `part_count`. The browser then
  asks `parts` for the URLs of the next four parts as it goes (up to 100 a call), and `complete`
  for the completion's URL at the end. `abort` aborts the upload from the API with the global key.
  `put` refuses a declared `size` over 100 MiB.
- **The browser key.** It is reused while it has more than 15 minutes, or the URL's lifetime if
  longer, still to live, so "Copy presigned link" (an hour) gets a key that outlives it. A URL never
  outlives its key: with an ephemeral lifetime under an hour, URLs last less than asked. The key is
  tied to whatever credential called (a browser session, a CLI token or an API key), so `presign`
  works for all three, and it ends with that credential as other ephemeral keys do. It shares the
  name `browser (ephemeral)` with the key `POST s3/credentials` gives a browser session, and can
  reuse one that isn't read-only. `sigv4.presign` signs the URLs (host only, unsigned payload).
- **The 10,000 cap** counts every key asked for, objects and folders together, and is checked
  before anything is deleted. Counting a huge folder exactly would mean listing all of it, so the
  refusal (409, with `limit` and `command`) says "more than 10,000" instead of the count. The
  command named is `aws s3 rm --recursive s3://{bucket}/{prefix} --profile flock`.
- **Buckets being deleted.** `GET orgs/{org}/buckets?deleting=true` lists them too, with their
  `deleted_at`; without it the listing is unchanged, so `flock s3 buckets list` still shows only
  live buckets.
- **Preview types.** A `get` presign may ask for `content_type`, sent as `response-content-type`,
  but only for `inline` and only images, PDF, `text/plain`, video and audio: a PDF or image stored
  as `binary/octet-stream` (as some tools upload) still previews, and nothing can be served as
  HTML. HTML files are previewed as text.
- **Listings** hide a folder's own marker. `Upstream` gained `list_objects`, `head`, `put_empty`,
  `delete_keys`, `create_upload` and `abort_upload`, which the API uses; the purge now uses the
  first and fourth too.
- **CORS.** Preflights from `FLOCK_WEB_URL`'s origin get the five methods, the requested headers
  that are S3's (`x-amz-*`, the forwarded ones and `authorization`), and a one-hour max age;
  other origins get 403 `AccessForbidden` with no CORS headers. Answers to that origin, errors
  included, expose `ETag`, `Content-Length`, `Content-Range`, `Content-Type`,
  `Content-Disposition`, `Accept-Ranges` and `Last-Modified`. The compose files now give the proxy
  `FLOCK_WEB_URL`.
- **The migration** refuses an org whose name is `storage` in any case, as the dashboard's
  `RESERVED` check does. Only the dashboard reserves the name: the API still creates an org
  called `storage` if asked directly, as it does for the dashboard's other page names.
- **Web routes.** The bucket, folder and object pages are one route,
  `storage/[org]/[bucket]/[...path]`, with `trailingSlash = 'ignore'` so a folder's URL keeps its
  `/`: the key is a folder when the URL ends in `/`. Key parts are encoded one by one. The browser
  never reaches Flock itself (its address and the user's token stay on the server), so listing
  more pages, filtering, presigning, deleting, creating folders and the history sheet go through
  the dashboard's own endpoints under `/api/storage/{org}/{bucket}/`, which pass the calls on.
- **Selection** covers folders too, not only objects: a selected folder is deleted as a prefix.
  Download shows for exactly one object. The upload queue runs three files at once, and a
  dropped folder's files keep their paths. Downloads open the presigned `attachment` URL.
- **The sidebar** knows whether S3 is on from `auth/config`, read in the `(app)` layout. The org
  settings page shows "Buckets moved to Storage" only when S3 is on. `OrgBuckets.svelte` is gone;
  its form became `storage/CreateBucket.svelte`, and `DeleteBucketDialog.svelte` moved to
  `components/storage/`.
- **Docs.** There is no separate page for the dashboard, so the S3 guide gained a "Storage tab"
  section; the API reference has the object routes and `presign`.

Checked by hand in a browser against moto with a local Postgres, API, proxy and web dev server:
the sidebar item and the collapsed rail's, `storage` refused as an org ID, creating a bucket
(with the name checked as typed), the history sheet, a bucket shown "being deleted", deleting a
bucket, uploading six small files and a 120 MiB file (8 parts, read back identical), dropping
files into a folder, creating a folder, paging 450 objects, the name filter, previews of a PNG,
text, JSON, Markdown (with its `onerror` stripped), a PDF and a malicious SVG (shown as an image,
its script not run), downloading, deleting a shift-click range of objects and a folder, switching
orgs from a storage page, and the org settings page keeping the key lifetimes without a Buckets
section. The same was repeated against Ceph with a `flock-test-…` prefix (create, uploads
including the 120 MiB multipart one, previews, deleting a folder through the dashboard's endpoint,
and deleting the bucket); afterwards the
account held only the bucket it had before.
