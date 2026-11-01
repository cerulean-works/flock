# Use your account and organization

Join Cerulean's Flock workspace, choose where your experiments belong, and manage your access from [the dashboard](https://flock.cerulean.works).

## Accounts, organizations and roles

Your **account** is your identity across Flock. An **organization** groups a team's projects, runs and storage. You can belong to several organizations and have a different role in each.

A **member** can read the organization's work, create projects, log runs and change their own runs. An **organization admin** can also manage members, projects and other people's runs. A **super admin** manages the Flock installation across organizations. [Permissions](permissions) has the complete matrix and protected last-admin rules.

Each organization has a display name and a fixed **ID**. The ID appears in URLs and is the wandb `entity`; the display name is what you normally read in the dashboard. Your **default organization** is used by CLI storage commands and by `wandb.init` when they do not name one.

## Join an organization

1. Ask an organization admin for an invitation addressed to your email, then open the shared link. Flock creates links but does not send the invitation email itself.
2. Create your account with a name and password, or sign in as the invited email if you already have an account. Use Google if offered with the invited address.
3. Accept the invitation and check that the organization appears in your account's **Organizations** list.
4. Open one of its projects to confirm access. If the invitation has expired or been used, ask the admin for another.

## Manage your account and keys

Open [your account page](https://flock.cerulean.works/account). Under **Organizations**, use **Make default** for the organization you normally work in. Later scripts can use that default without embedding an organization ID.

Use **Change profile** or the password controls to update your account. Under **Command line sign-ins**, review and revoke machines you no longer use. [CLI sign-in](../cli/sign-in) explains the machine authorization flow and how SDK setup creates a separate wandb key.

The account page also manages keys for particular clients:

| Section | Purpose |
|---|---|
| **Keys** | wandb keys for experiment logging. `flock login` can set one up for the SDK. |
| **S3 keys** | Persistent keys for storage clients that cannot renew temporary credentials. |
| **Command line sign-ins** | Machine CLI tokens, used by Flock clients and to request ephemeral S3 keys. |

Review [S3 authentication](../cli/s3-access#how-authentication-works) before creating storage keys. It explains why ephemeral keys are the usual choice and how persistent keys differ. Full key values are shown when created; save them in the intended client's secret store and revoke unused entries.

## Invite and manage members

Organization admins use the organization's settings:

1. Under **Invite people**, enter the colleague's email and choose **Member** or **Admin**.
2. Create and copy the invitation link, then share it privately with that colleague.
3. Check **Members** after acceptance. Use the member controls to change a role or remove access, or withdraw an unused invitation under **Pending**.
4. For a forgotten password, create a reset link through the admin controls and share it privately. Ask the person to confirm they can sign in again.

Removing membership leaves the person's runs in the organization. An admin who resets a member's password can gain that account's access to other organizations too, so grant that role with care. [Authentication](authentication) covers invitation expiry, reset authority and revocation rules.

Super admins manage server-wide users on the **Super admin** page. Account deletion is permanent and leaves existing runs without an owner. Creating the first administrator belongs to [deploying a new installation](deploy#create-the-first-account), rather than ordinary Cerulean onboarding.
