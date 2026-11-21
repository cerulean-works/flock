/** The Datasets tab (013): its URLs, the calls it makes, and how cells and sizes read. */

import { resolve } from '$app/paths';
import type { ResolvedPathname } from '$app/types';
import { type Scope, asScope, scopeQuery } from './scope.ts';
import { objectHref } from './storage.ts';
import type { Cell } from './types.ts';
import { bytes } from './utils.ts';
import { projectHref } from './workspace/url.ts';

/** The org's managed bucket, as S3 clients name it. */
export const DATASETS_BUCKET = 'flock-datasets';
/** Where projects' datasets live in it, `-projects/{project id}/` (D160). */
export const PROJECTS = '-projects';
/** How long a deleted dataset waits in the trash. */
export const TRASH_DAYS = 14;

const segments = (path: string) => path.split('/').map(encodeURIComponent).join('/');

// ----------------------------------------------------------------------------------------------
// URLs: `/datasets/{org}/{path…}` for global, `/{org}/{project}/datasets/{path…}` for a
// project's (D169); a folder or a dataset (the API decides).

/**
 * A scope's root (an org alone is its global scope), or a folder or dataset in it. Each part of
 * the path is encoded on its own.
 */
export function datasetsHref(scope: Scope | string, path = '') {
	const { org, project } = asScope(scope);
	const root =
		project === null
			? resolve('/(app)/datasets/[org]/[...path]', { org, path: '' }).replace(/\/$/, '')
			: `${projectHref(org, project)}/datasets`;
	return `${root}${path ? `/${segments(path)}` : ''}` as ResolvedPathname;
}

/** A dataset at one version (forks and blends link to their sources' versions, 023). */
export const datasetVersionHref = (scope: Scope | string, path: string, version: number) =>
	`${datasetsHref(scope, path)}?version=${version}` as ResolvedPathname;

/**
 * A dataset as forks and blends name it, across scopes (023 with 022): its path for the
 * workspace's, `project:path` for a project's, as `flock.hub` names them (D162).
 */
export const datasetRef = (scope: Scope | string, path: string) => {
	const { project } = asScope(scope);
	return project === null ? path : `${project}:${path}`;
};

/** The scope and path of a fork's or blend's name (`datasetRef`): a `:` before any `/` names a project. */
export function refScope(org: string, ref: string): { scope: Scope; path: string } {
	const colon = ref.indexOf(':');
	const slash = ref.indexOf('/');
	if (colon > 0 && (slash < 0 || colon < slash))
		return { scope: { org, project: ref.slice(0, colon) }, path: ref.slice(colon + 1) };
	return { scope: { org, project: null }, path: ref };
}

/** The page of a dataset named as forks and blends name it (`datasetRef`), at `version` if given. */
export function refHref(org: string, ref: string, version?: number) {
	const { scope, path } = refScope(org, ref);
	return version === undefined
		? datasetsHref(scope, path)
		: datasetVersionHref(scope, path, version);
}

/** A file of flock-datasets, in the Storage tab. */
export const fileHref = (org: string, key: string) => objectHref(org, DATASETS_BUCKET, key);

/** `s3://flock-datasets/{path}`, as Lance and other S3 clients take it. */
export const datasetUri = (path: string) => `s3://${DATASETS_BUCKET}/${path}`;

/** One link of the breadcrumbs. */
export interface Crumb {
	label: string;
	href: ResolvedPathname;
}

/**
 * Datasets › org › folders › dataset (the last one the page itself), none at the root; in a
 * project, Project › Datasets › folders › dataset, at its root too.
 */
export function crumbs(scope: Scope | string, orgLabel: string, path: string): Crumb[] {
	return scopeCrumbs(scope, orgLabel, 'Datasets', path, datasetsHref);
}

/** The breadcrumbs of a scope's datasets or models, which `href` links to. */
export function scopeCrumbs(
	scope: Scope | string,
	orgLabel: string,
	tab: string,
	path: string,
	href: (scope: Scope, path?: string) => ResolvedPathname
): Crumb[] {
	const s = asScope(scope);
	if (s.project === null && !path) return [];
	const out: Crumb[] =
		s.project === null
			? [
					{ label: tab, href: href(s) },
					{ label: orgLabel, href: href(s) }
				]
			: [
					{ label: s.project, href: projectHref(s.org, s.project) },
					{ label: tab, href: href(s) }
				];
	const parts = path ? path.split('/') : [];
	for (let i = 0; i < parts.length; i++) {
		out.push({ label: parts[i], href: href(s, parts.slice(0, i + 1).join('/')) });
	}
	return out;
}

/** The folder a path is in (`eval/mmlu` → `eval`, `mmlu` → the root). */
export const parentOf = (path: string) => path.slice(0, Math.max(0, path.lastIndexOf('/')));

/** The last part of a path. */
export const nameOf = (path: string) => path.slice(path.lastIndexOf('/') + 1);

/** Whether `path` is `folder` or under it (the root holds everything). */
export const within = (path: string, folder: string) =>
	folder === '' || path === folder || path.startsWith(`${folder}/`);

/** A dataset or folder checked in a listing (D130), for the Move and Delete dialogs. */
export interface DatasetItem {
	path: string;
	folder: boolean;
}

/** An item's path as the dialogs show it, folders ending in `/`. */
export const itemPath = (item: DatasetItem) => `${item.path}${item.folder ? '/' : ''}`;

/** An item's name as the dialogs show it, folders ending in `/`. */
export const itemName = (item: DatasetItem) => `${nameOf(item.path)}${item.folder ? '/' : ''}`;

// ----------------------------------------------------------------------------------------------
// The dashboard's endpoints (`src/routes/api/datasets`), which call Flock as the signed-in user.

/** Flock's answer to a refused call: its message, and what else it said (`taken`, `version`). */
export class DatasetError extends Error {
	status: number;
	body: Record<string, unknown>;

	constructor(status: number, body: Record<string, unknown>) {
		super(String(body.message ?? body.error ?? `Flock answered ${status}`));
		this.status = status;
		this.body = body;
	}
}

/**
 * The dashboard's endpoint for one of an org's dataset routes, with its query and the scope's
 * `project` (D163).
 */
export function datasetsApi(
	scope: Scope | string,
	op: string,
	query: Record<string, string | number | string[] | null | undefined> = {}
): string {
	const { org } = asScope(scope);
	const search = new URLSearchParams();
	for (const [name, value] of Object.entries({ ...query, ...scopeQuery(scope) })) {
		if (value === null || value === undefined) continue;
		for (const v of Array.isArray(value) ? value : [value]) search.append(name, String(v));
	}
	const qs = search.toString();
	return `/api/datasets/${encodeURIComponent(org)}/${op}${qs ? `?${qs}` : ''}`;
}

/** A call to one of the dashboard's dataset endpoints; refusals throw a `DatasetError`. */
export async function datasetCall<T>(
	url: string,
	init: {
		method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
		body?: unknown;
		signal?: AbortSignal;
	} = {}
): Promise<T> {
	const res = await fetch(url, {
		method: init.method ?? 'GET',
		signal: init.signal,
		...(init.body === undefined
			? {}
			: { headers: { 'content-type': 'application/json' }, body: JSON.stringify(init.body) })
	});
	const json = res.status === 204 ? null : await res.json().catch(() => null);
	if (!res.ok) throw new DatasetError(res.status, (json ?? {}) as Record<string, unknown>);
	return json as T;
}

// ----------------------------------------------------------------------------------------------
// Rules the API enforces, checked as you type.

const SEGMENT = /^[A-Za-z0-9._-]+$/;

/** Why a path can't be a dataset's new path (D95), or null. */
export function pathError(path: string): string | null {
	if (!path) return 'Enter a path';
	if (path.length > 512) return 'Use at most 512 characters';
	const parts = path.split('/');
	if (parts.some((p) => !SEGMENT.test(p) || p === '.' || p === '..'))
		return 'Use letters, digits, dots, hyphens and underscores, with / between folders';
	if (parts.includes('_versions')) return 'A folder can’t be named _versions';
	return null;
}

const TAG = /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/;

/** Why a tag name can't be used (D96), or null. */
export function tagError(name: string): string | null {
	if (!name) return 'Enter a name';
	if (!TAG.test(name))
		return name.length > 100
			? 'Use at most 100 characters'
			: 'Use letters, digits, dots, hyphens and underscores, starting with a letter or digit';
	if (/^\d+$/.test(name)) return 'Add a letter, since numbers alone are versions';
	return null;
}

// ----------------------------------------------------------------------------------------------
// Display

/** Whole days left before a deleted dataset is removed, at least 0. */
export const daysLeft = (expiresAt: number, now: number) =>
	Math.max(0, Math.ceil((expiresAt - now) / 86_400));

/** A cell as one line of text, for titles and the cells that are only text. */
export function cellText(cell: Cell): string {
	switch (cell.k) {
		case 'null':
			return 'null';
		case 'num':
			return cell.label === undefined ? String(cell.v) : `${cell.v} (${cell.label})`;
		case 'bool':
			return String(cell.v);
		case 'str':
		case 'time':
		case 'json':
			return cell.v;
		case 'image':
			return `${cell.mime.replace('image/', '').toUpperCase()} image, ${bytes(cell.size)}`;
		case 'bin':
			return bytes(cell.size);
		case 'blob':
			return cell.size === null ? 'blob' : `blob, ${bytes(cell.size)}`;
		case 'vec':
			return `${cell.dtype}[${cell.len}]`;
	}
}

/** Whether a cell was cut short, so an opened row reads it in full. */
export const cellCut = (cell: Cell) => (cell.k === 'str' || cell.k === 'json') && !!cell.len;

/** A string as Python writes it (project names may hold quotes and spaces). */
export const pyString = (text: string) => JSON.stringify(text);

/**
 * The snippet that writes a dataset: into the org's flock-datasets (section 1 of 013), or into
 * a project's datasets, found by name with `location` (D164).
 */
export function writeSnippet(scope: Scope | string) {
	const { org, project } = asScope(scope);
	if (project !== null)
		return [
			'import lance',
			'from flock.hub import datasets',
			'',
			`where = datasets.location("eval/mmlu", project=${pyString(project)})`,
			'lance.write_dataset(table, where.uri, storage_options=where.storage_options)'
		].join('\n');
	return [
		'import lance',
		'from flock.hub import s3',
		'',
		`creds = s3.credentials("${org}")`,
		'lance.write_dataset(',
		`    table, creds.uri("${DATASETS_BUCKET}", "eval", "mmlu"), storage_options=creds.storage_options()`,
		')'
	].join('\n');
}

/**
 * The snippet that opens a dataset with Lance, with a long-lived key filled in by hand. `uri` is
 * where it really is, which for a project's dataset only the API knows (D160).
 */
export function readSnippet(
	path: string,
	endpoint: string,
	region: string,
	uri = datasetUri(path)
): string {
	const options: [string, string][] = [
		['aws_endpoint', endpoint],
		['aws_access_key_id', 'YOUR_ACCESS_KEY_ID'],
		['aws_secret_access_key', 'YOUR_SECRET_ACCESS_KEY'],
		['aws_region', region],
		['aws_virtual_hosted_style_request', 'false'],
		...(endpoint.startsWith('http://') ? ([['allow_http', 'true']] as [string, string][]) : [])
	];
	return [
		'import lance',
		'',
		'ds = lance.dataset(',
		`    "${uri}",`,
		'    storage_options={',
		...options.map(([k, v]) => `        "${k}": "${v}",`),
		'    },',
		')'
	].join('\n');
}

/**
 * The pages a pager links to: the first, the last, and the current one with its neighbours,
 * `null` where pages are left out. A gap of one page shows that page instead.
 */
export function pageNumbers(current: number, count: number): (number | null)[] {
	const keep = new Set([1, count, current - 1, current, current + 1]);
	const out: (number | null)[] = [];
	for (let n = 1; n <= count; n++) {
		if (keep.has(n) || (keep.has(n - 1) && keep.has(n + 1))) out.push(n);
		else if (out.at(-1) !== null) out.push(null);
	}
	return out;
}
