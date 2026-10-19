import { resolve } from '$app/paths';
import type { ResolvedPathname } from '$app/types';
import type { RunQuery, SortOrder } from '#lib/types.ts';

// App URLs: an entity's projects, and workspaces with the run-page query they carry.

export const DEFAULT_SORT = 'created_at';
export const DEFAULT_ORDER: SortOrder = 'desc';
/** Runs per table page, unless the URL picks another of `PAGE_SIZES`. */
export const PAGE_SIZE = 100;
/** The page sizes offered; Flock serves at most 500 runs per snapshot. */
export const PAGE_SIZES = [25, 50, 100, 250, 500] as const;

/** Run-page query from URL search params. Only `page`, `sort`, `order` and `size` affect data. */
export function readRunQuery(params: URLSearchParams): RunQuery {
	const page = Math.max(1, Math.floor(Number(params.get('page')) || 1));
	const sort = params.get('sort') || DEFAULT_SORT;
	const order: SortOrder = params.get('order') === 'asc' ? 'asc' : 'desc';
	const asked = Number(params.get('size'));
	const size = (PAGE_SIZES as readonly number[]).includes(asked) ? asked : PAGE_SIZE;
	return { page, sort, order, size };
}

export function runQueryString(q: RunQuery): string {
	return `page=${q.page}&sort=${encodeURIComponent(q.sort)}&order=${q.order}&size=${q.size}`;
}

/** Search params for a workspace URL, omitting defaults so URLs stay short. */
export function applyRunQuery(params: URLSearchParams, q: RunQuery) {
	if (q.page === 1) params.delete('page');
	else params.set('page', String(q.page));
	if (q.sort === DEFAULT_SORT && q.order === DEFAULT_ORDER) {
		params.delete('sort');
		params.delete('order');
	} else {
		params.set('sort', q.sort);
		params.set('order', q.order);
	}
	if (q.size === PAGE_SIZE) params.delete('size');
	else params.set('size', String(q.size));
}

/** Flock's sort syntax: `-column` for descending. The name column sorts by display name. */
export function flockSort(q: RunQuery): string {
	const key = q.sort === 'name' ? 'display_name' : q.sort;
	return q.order === 'desc' ? `-${key}` : key;
}

/** An entity's projects page. */
export const entityHref = (entity: string) => resolve('/(app)/[entity]', { entity });

/** An organization's members, invitations and settings; `section` jumps to one of them. */
export const orgHref = (org: string, section?: 'budget') =>
	`${resolve('/(app)/orgs/[org]', { org })}${section ? `#${section}` : ''}` as ResolvedPathname;

/** The current page with one search param set, or removed when null: tabs and filters. */
export function withParam(
	url: { pathname: string; search: string },
	key: string,
	value: string | null
): ResolvedPathname {
	const search = new URLSearchParams(url.search);
	if (value == null) search.delete(key);
	else search.set(key, value);
	const qs = search.toString();
	return `${url.pathname}${qs ? `?${qs}` : ''}` as ResolvedPathname; // already base-relative
}

/** A project's workspace, without search params. */
export const projectHref = (entity: string, project: string) =>
	resolve('/(app)/[entity]/[project]', { entity, project });

/** A run's own page: its overview, charts, logs and files. */
export const runHref = (entity: string, project: string, run: string) =>
	resolve('/(app)/[entity]/[project]/runs/[run]', { entity, project, run });

/** URL of a workspace with the given search params; base-path aware. */
export function workspaceHref(
	entity: string,
	project: string,
	params: URLSearchParams
): ResolvedPathname {
	const search = params.toString();
	const route =
		`/(app)/[entity]/[project]${search ? `?${search}` : ''}` as `/(app)/[entity]/[project]?${string}`;
	return resolve(route, { entity, project });
}

/** The current workspace URL with some search params set (string) or removed (null). */
export function workspaceHrefWith(
	entity: string,
	project: string,
	search: string,
	changes: Record<string, string | null>
): ResolvedPathname {
	const params = new URLSearchParams(search);
	for (const [k, v] of Object.entries(changes)) {
		if (v == null) params.delete(k);
		else params.set(k, v);
	}
	return workspaceHref(entity, project, params);
}
