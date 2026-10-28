# Permissions

Who can do what, by role. A user's role is per organization: they can be an admin of one organization and a member of another. Super admins act as admins of every organization, whether or not they're members. [Roles](accounts#roles) explains the roles.

## Matrix

| Action | Member | Org admin | Super admin |
|---|---|---|---|
| Read the organization's projects, runs, curves, logs and files | yes | yes | yes |
| List the organization's members | yes | yes | yes |
| Log runs with the SDK, which also creates projects | yes | yes | yes |
| Resume or write to an existing run with the SDK | own runs | any run | any run |
| Create a project with the API | yes | yes | yes |
| Rename a project or change its description | no | yes | yes |
| Delete a project | no | yes | yes |
| Edit, move or delete runs | own runs | any run | any run |
| Invite people, list and withdraw invitations | no | yes | yes |
| Change members' roles, remove members | no | yes | yes |
| Leave the organization | yes | yes, unless the last admin | yes, unless the last admin |
| Change the organization's display name, or delete it | no | yes | yes |
| Read the home feed, GPU prices and the budget | yes | yes | yes |
| Set GPU prices, the budget and the currency | no | yes | yes |
| See budget warnings, dismiss them for the organization, or show them again | no | yes | yes |
| See deleted projects' spend, and leave it out of the budget | no | yes | yes |
| Correct a run's GPUs | own runs | any run | any run |
| Create organizations | no | no | yes |
| Create a password reset link | no | for members of their organizations, except super admins | for anyone |
| List users, grant or revoke super admin, delete accounts | no | no | yes |
| Manage their own profile, avatar, password and wandb keys | yes | yes | yes |
| Set or remove the organization's avatar | no | yes | yes |

"Own runs" are the runs whose `owner` is the user: the runs they logged. Runs without an owner, logged before accounts existed or by a deleted account, can only be changed by admins.

## What a refusal looks like

| Situation | Response |
|---|---|
| No credentials, or invalid ones | 401 |
| The organization is one you don't belong to | 404, as if it didn't exist. The same goes for its projects and runs. |
| Your role doesn't allow the action | 403 |
| Runs you don't own, through the API | 403, with their run ids in `ids`. Bulk operations change nothing. |
| A run you don't own, through the SDK | The SDK fails with `run <path> belongs to another user` (or 403 for `file_stream`). |
| An organization you don't belong to, through the SDK | The SDK fails with `organization <name> not found`. |

## Rules that override roles

- **An organization keeps its last admin.** Demoting or removing its last admin answers 409, even when the admin removes themselves. Only deleting that admin's account leaves the organization without one.
- **The server always keeps a super admin.** Demoting or deleting the last super admin answers 409.
- **Invitations are tied to an email.** Only the account with the invited email can accept one.
- **Org admins can't reset a super admin's password.** That would let them take over the server.

Roles are checked on every request, so changes apply at once. One exception: an open [live stream](api/dashboard#the-live-stream) was authorized when it opened, and keeps running after its user loses access.
