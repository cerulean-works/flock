# 021: check public access against the path being read

| | |
| --- | --- |
| Status | implemented and independently reviewed |
| Builds on | 015 public projects, 016 project creators, 019 bulk select |
| Keeps | D110 to D118, D130 to D135 |

## Goal

Keep private dataset rows and private run metadata fields behind their existing access rules.
Project publishing rights stay as specified in 015 and 016. New dataset paths become private
after the visibility migration; existing public paths retain their access.

## Design decisions

### D151. Dataset paths cannot contain dot segments

A version-pinned read can resolve an object path differently from the exact path used for its
privacy flag. Reject `.` and `..` segments before reading a dataset. New destination paths
already reject them. Case, ordinary dots in names, and exact-path privacy rules stay intact.

### D152. Metadata aliases receive the metadata filter

The local file backend resolves `./wandb-metadata.json` to the same file as the canonical name.
Apply the public-field filter when the validated path components name that file, including
encoded dot and repeated-slash spellings. Members still read the original uploaded content.

### D153. Removing membership closes the organization's project streams

Close every project stream in the organization through the existing transactional event bus.
Remaining members reconnect and replay normally. This supersedes stage 003's documented
exception for streams opened before removal. Recheck membership after subscribing to cover
a removal that committed while the connection opened. Stop initial replay if the subscription
closed while its data was being read. Hold the existing exclusive organization lock through
removal, so project creation cannot escape the snapshot of streams to close. Per-user
subscription tracking is outside this change.

### D154. Missing orgs and private datasets have the same refusal

An outsider probing a dataset must not distinguish an unknown organization from a private
path by its error message. Both answer the existing dataset-not-found response. Actual org
members keep their read access and normal dataset errors.

### D155. Public datasets require exact grants after a staged cutover

An additive migration adds public grants and an org inventory marker. Until the contract
migration runs, both releases retain D115's behavior. The contract waits for older API and
proxy instances to retire cleanly, including ones whose heartbeats have expired. A crashed
worker requires operator confirmation that it cannot resume before its row is removed. The
contract captures the legacy policy for inventory, then constrains the legacy public flag to
false. Even an unexpectedly resumed old reader therefore fails closed, and its old sweep
cannot reopen the flag. Future orgs are ready for explicit grants; the existing contract check
also prevents an older binary from restarting.

Existing orgs temporarily refuse anonymous dataset reads and dataset operations while their
inventory is pending. The inventory grants only existing dataset paths in previously open
orgs, excluding exact private flags, then marks the org ready in the same transaction. The Storage tab and proxy also refuse new writes while inventory is pending. Nested datasets
are inventoried beneath their parents. Failed inventories publish no partial result and are retried. New paths and HF imports are private.
Writes begun before cutover may finish under their original policy; the launch criterion is
that the contract has applied and every org's inventory has completed. Upstream direct writers
must pause for the cutover, since the proxy cannot govern credentials issued outside Flock.

### D156. Moves preserve public grants without broadening private destinations

Private destination flags take precedence. Move and restore grant a public source's destination
only after object copying succeeds. An implicit-private source makes a destination private
before any object lands there, even if a public grant remains at that destination. Every nested dataset is handled independently, including children of datasets and
restored trees. Permissions remain attached to exact paths across versions and deletes; a rename removes source grants.

### D157. Permission-sensitive responses cannot be cached

API content and dashboard HTML/data responses send `Cache-Control: private, no-store` so a
previous public response is not reused after a permission change. Digest-addressed avatars
retain their immutable caching. This cannot remove copies already downloaded by a reader.

## Plan

Reproduce both cases through the HTTP API using synthetic data, fix the boundary checks, and
run the existing public access and dataset tests before the full repository checks.

## How it was built

Two metadata alias cases and two version-pinned dataset path cases failed before the changes.
The fixes reuse the existing file-path validation and dataset-path validation. Tests use local
PostgreSQL and Moto; no production state is changed.

## Known gaps

Previously downloaded data cannot be recalled by changing visibility. A public project's
configs, logs and uploaded files still publish their logged content under D113.
