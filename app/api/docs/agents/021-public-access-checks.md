# 021: check public access against the path being read

| | |
| --- | --- |
| Status | built, independently reviewed |
| Builds on | 015 public projects, 016 project creators, 019 bulk select |
| Keeps | D110 to D118, D130 to D135 |

## Goal

Keep private dataset rows and private run metadata fields behind their existing access rules.
Public dataset defaults and project publishing rights stay as specified in 015 and 016.

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
