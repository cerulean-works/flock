# HTTP API overview

The tracker's HTTP API lives under `/api/v1`. This page covers what every endpoint has in common: credentials, request and response conventions, and an index of the endpoints. The tracker also serves interactive documentation of the API at `/docs`, and its OpenAPI schema at `/openapi.json`.

The wandb protocol endpoints that the SDK calls (`/graphql`, `/files/...` and `/storage/...`) aren't part of this API. See [wandb compatibility](../wandb-compatibility#protocol-endpoints).

## Credentials

Every endpoint needs credentials, except the public ones marked in the index below. Send one of these:

| Credential | How to send it |
|---|---|
| Access token | `Authorization: Bearer <access_token>` |
| API key | `Authorization: Bearer <key>`, or `x-api-key: <key>`, or HTTP Basic auth with any user name and the key as the password. Basic auth is what the `wandb` SDK sends, as `api:<key>`. |

A Bearer value with two dots is read as an access token, and anything else as an API key. Missing, unknown, expired or revoked credentials answer 401. [Accounts, organizations and roles](../accounts#sessions) explains how to get each kind.

## Conventions

- **Bodies** are JSON, sent with `Content-Type: application/json`. Most bodies reject fields they don't know, with 422.
- **Responses** are JSON, except run files, the [workspace snapshot](dashboard#the-workspace-snapshot) (binary) and the [event stream](dashboard#the-live-stream).
- **Times** are Unix timestamps in seconds, as floats, such as `1792282202.83`.
- **Identifiers:** organizations and projects are addressed by name, runs by their run id, and users, API keys and invitations by an integer `id`.
- **Missing and hidden resources** both answer 404. A project in an organization you don't belong to looks exactly like one that doesn't exist.
- **Non-finite numbers** (`NaN` and infinity) come back as `null`.
- **Queries** that select runs have a `GET` form that takes query parameters, and a `POST .../query` form that takes the same fields as a JSON body.

Errors are described in [Errors and status codes](../errors).

## Endpoints

All paths are relative to `/api/v1`. In paths, `{entity}` is an organization's name.

| Endpoint | Purpose | Page |
|---|---|---|
| `GET /auth/config` | What a sign-in page should offer. Public. | [Sign-in](auth#auth-config) |
| `POST /auth/signup` | Create the first account. Public. | [Sign-in](auth#signup) |
| `POST /auth/login`, `POST /auth/refresh` | Sign in, and refresh tokens. Public. | [Sign-in](auth#login) |
| `POST /auth/logout` | End the current session. | [Sign-in](auth#logout) |
| `GET /auth/google/start`, `GET /auth/google/callback`, `POST /auth/google/exchange` | Sign in with Google. Public. | [Sign-in](auth#google) |
| `GET /me`, `PATCH /me` | Your profile and default organization. | [Accounts](auth#me) |
| `POST /me/password` | Change your password. | [Accounts](auth#change-password) |
| `GET /me/api-keys`, `POST /me/api-keys`, `DELETE /me/api-keys/{id}` | Your API keys. | [Accounts](auth#api-keys) |
| `PUT /me/avatar`, `DELETE /me/avatar`, `POST /me/avatar/gravatar` | Your avatar. | [Accounts](auth#avatars) |
| `GET /avatars/{digest}` | An avatar image. Public. | [Accounts](auth#avatars) |
| `GET /users`, `PATCH /users/{id}`, `DELETE /users/{id}` | All accounts. Super admins only. | [Accounts](auth#users) |
| `POST /users/{id}/password-reset` | Create a password reset link. | [Accounts](auth#create-reset) |
| `GET /password-resets/{token}`, `POST /password-resets/{token}` | Inspect and redeem a reset link. Public. | [Accounts](auth#redeem-reset) |
| `GET /orgs`, `POST /orgs` | Your organizations, and creating one. | [Organizations](orgs#organizations) |
| `GET /orgs/{org}`, `PATCH /orgs/{org}`, `DELETE /orgs/{org}` | One organization. | [Organizations](orgs#organizations) |
| `PUT /orgs/{org}/avatar`, `DELETE /orgs/{org}/avatar` | The organization's avatar. | [Organizations](orgs#organizations) |
| `GET /orgs/{org}/members`, `PATCH /orgs/{org}/members/{id}`, `DELETE /orgs/{org}/members/{id}` | Members and their roles. | [Organizations](orgs#members) |
| `GET /orgs/{org}/invites`, `POST /orgs/{org}/invites`, `DELETE /orgs/{org}/invites/{id}` | Pending invitations. | [Organizations](orgs#invitations) |
| `GET /invites/{token}`, `POST /invites/{token}/accept` | Inspect and accept an invitation. Public. | [Organizations](orgs#invitation-links) |
| `GET /projects`, `POST /projects` | Your projects, and creating one. | [Projects](projects#projects) |
| `GET /projects/{entity}/{project}`, `PATCH ...`, `DELETE ...` | One project. | [Projects](projects#projects) |
| `GET .../runs/{run}`, `PATCH .../runs/{run}`, `DELETE .../runs/{run}` | One run. | [Projects](projects#runs) |
| `POST .../runs/update`, `POST .../runs/delete`, `POST .../runs/move` | Change many runs. | [Projects](projects#bulk-operations) |
| `GET .../runs/{run}/logs` | A run's console output. | [Projects](projects#run-logs) |
| `GET .../runs/{run}/files`, `GET .../runs/{run}/files/{name}` | A run's files. | [Projects](projects#run-files) |
| `GET .../columns` | The run table's columns. | [Queries](queries#get-columns) |
| `GET .../runs`, `POST .../runs/query` | Filter, sort and page the run table. | [Queries](queries#the-run-table) |
| `GET .../history`, `POST .../history/query` | Curves of many runs. | [Queries](queries#curves) |
| `GET .../runs/{run}/history` | Curves of one run. | [Queries](queries#curves-of-one-run) |
| `GET .../workspace` | Binary snapshot of a dashboard page. | [Snapshot](dashboard#the-workspace-snapshot) |
| `GET .../stream` | Server-Sent Events with live updates. | [Snapshot](dashboard#the-live-stream) |
| `GET /feed` | Recent activity across your projects. | [Home](home#feed) |
| `GET /orgs/{org}/billing`, `PATCH /orgs/{org}/billing` | GPU prices and monthly budgets. | [Home](home#billing) |
| `GET /orgs/{org}/budget` | A month's spend against the budget. | [Home](home#budget) |

`.../` stands for `/projects/{entity}/{project}/`. `GET /healthz`, outside `/api/v1`, answers `{"ok":true}` without credentials.
