# Import W&B runs

Copy an existing W&B entity's experiments into a Flock organization.

## Before you start

You need a Flock super admin account, the W&B entity name and a W&B key that can read it. Invite the team before importing if you want authors matched to their Flock accounts. Review [what is copied](import-behavior#what-is-copied), especially the file and artifact limits.

## Start and check an import

1. Create an organization with **Import runs from W&B** enabled, either during first setup or through **New organization** on the **Super admin** page. For an existing organization, open its settings and the **Import from W&B** section.
2. Enter the source entity and its W&B API key. Use the source server's API URL when importing from a self-managed W&B installation.
3. Follow progress in the organization's settings. Projects appear as they are copied.
4. When the state is `finished`, check the failed-run count, then open a known project and compare a run's metrics and timestamps with W&B.

## Resume an interrupted import

1. Open the organization's import progress.
2. Supply the W&B key again and resume. Flock does not store that key across restarts.
3. Check the final status and any failed or skipped files.

Already present project/run IDs are skipped, so resuming preserves completed work. Existing runs are not refreshed with newer W&B data. See [Import behavior](import-behavior) for ownership, state mapping and failure details, and the [Organization API](api/orgs#imports) for scripted imports.
