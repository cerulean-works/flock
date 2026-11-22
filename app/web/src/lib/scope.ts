/**
 * Where a dataset or model lives (022, D159): the org's own, global scope, or one project of the
 * org. The scope picks the API's `project` parameter (D163) and the pages a listing links to:
 * `/datasets/{org}/…` and `/models/{org}/…` for global, `/{org}/{project}/datasets/…` and
 * `/{org}/{project}/models/…` for a project (D169).
 */
export interface Scope {
	org: string;
	/** The project's name; null for global. */
	project: string | null;
}

/** An org's global scope, or the scope itself. */
export const asScope = (scope: Scope | string): Scope =>
	typeof scope === 'string' ? { org: scope, project: null } : scope;

/** The API's `project` query parameter: absent for global. */
export function scopeQuery(scope: Scope | string): { project?: string } {
	const { project } = asScope(scope);
	return project === null ? {} : { project };
}

/** How the scope pickers name a scope. */
export const scopeLabel = (project: string | null) => project ?? 'Workspace';
