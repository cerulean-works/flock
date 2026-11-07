/**
 * The runs table filters each project was last left with, kept in this browser, so a project
 * opens filtered as the viewer left it.
 *
 * Filters live in the workspace URL. A URL without them means one of two things: the viewer is
 * arriving at the project (from the sidebar, another project or a run's page), and gets the
 * filters back; or they cleared the filters while on it, which is remembered too.
 */

import { readFilters, writeFilters } from './filters.ts';

const filtersKey = (project: string) => `flock:filters:${project}`;

/** The project (`entity/project`) whose workspace this tab is showing, if any. */
let showing: string | null = null;

function read(key: string): string | null {
	try {
		return localStorage.getItem(key);
	} catch {
		return null;
	}
}

function write(key: string, value: string | null) {
	try {
		if (value == null) localStorage.removeItem(key);
		else localStorage.setItem(key, value);
	} catch {
		// Kept for this visit only, which is to say not at all.
	}
}

/** Filters as kept: well formed, or none. */
const clean = (param: string | null) => writeFilters(readFilters(param));

/**
 * Note that a project's workspace is shown with `params`, and keep its filters. Returns the
 * filters (a `filters` param) to show it with instead, when the viewer is arriving at it
 * without any and left it filtered.
 */
export function rememberFilters(
	entity: string,
	project: string,
	params: { has(name: string): boolean; get(name: string): string | null }
): string | null {
	const at = `${entity}/${project}`;
	const arriving = showing !== at;
	showing = at;
	const key = filtersKey(at);
	if (params.has('filters')) write(key, clean(params.get('filters')));
	else if (!arriving) write(key, null);
	else return clean(read(key));
	return null;
}

/** Whether this tab already keeps track of a project's filters (it loaded it here). */
export const showingProject = (entity: string, project: string) =>
	showing === `${entity}/${project}`;

/** The workspace was left: going back to it is arriving again. */
export function leftWorkspace() {
	showing = null;
}
