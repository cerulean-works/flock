/** The Datasets tab (013): its URLs, the calls it makes, and how cells and sizes read. */

import { resolve } from '$app/paths';
import type { ResolvedPathname } from '$app/types';
import { objectHref } from './storage.ts';
import type { Cell } from './types.ts';
import { bytes } from './utils.ts';

/** The org's managed bucket, as S3 clients name it. */
export const DATASETS_BUCKET = 'flock-datasets';
/** How long a deleted dataset waits in the trash. */
export const TRASH_DAYS = 14;

const segments = (path: string) => path.split('/').map(encodeURIComponent).join('/');

// ----------------------------------------------------------------------------------------------
// URLs: `/datasets/{org}` and `/datasets/{org}/{path…}`, a folder or a dataset (the API decides).

/** The bucket's root, or a folder or dataset in it. Each part of the path is encoded on its own. */
export const datasetsHref = (org: string, path = '') =>
	`${resolve('/(app)/datasets/[org]/[...path]', { org, path: '' }).replace(/\/$/, '')}${
		path ? `/${segments(path)}` : ''
	}` as ResolvedPathname;

/** A file of flock-datasets, in the Storage tab. */
export const fileHref = (org: string, key: string) => objectHref(org, DATASETS_BUCKET, key);

/** `s3://flock-datasets/{path}`, as Lance and other S3 clients take it. */
export const datasetUri = (path: string) => `s3://${DATASETS_BUCKET}/${path}`;

/** One link of the breadcrumbs. */
export interface Crumb {
	label: string;
	href: ResolvedPathname;
}

/** Datasets › org › folders › dataset (the last one the page itself); none at the root. */
export function crumbs(org: string, orgLabel: string, path: string): Crumb[] {
	if (!path) return [];
	const out: Crumb[] = [
		{ label: 'Datasets', href: datasetsHref(org) },
		{ label: orgLabel, href: datasetsHref(org) }
	];
	const parts = path.split('/');
	for (let i = 0; i < parts.length; i++) {
		out.push({ label: parts[i], href: datasetsHref(org, parts.slice(0, i + 1).join('/')) });
	}
	return out;
}

/** The folder a path is in (`eval/mmlu` → `eval`, `mmlu` → the root). */
export const parentOf = (path: string) => path.slice(0, Math.max(0, path.lastIndexOf('/')));

/** The last part of a path. */
export const nameOf = (path: string) => path.slice(path.lastIndexOf('/') + 1);

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

/** The dashboard's endpoint for one of an org's dataset routes, with its query. */
export function datasetsApi(
	org: string,
	op: string,
	query: Record<string, string | number | string[] | null | undefined> = {}
): string {
	const search = new URLSearchParams();
	for (const [name, value] of Object.entries(query)) {
		if (value === null || value === undefined) continue;
		for (const v of Array.isArray(value) ? value : [value]) search.append(name, String(v));
	}
	const qs = search.toString();
	return `/api/datasets/${encodeURIComponent(org)}/${op}${qs ? `?${qs}` : ''}`;
}

/** A call to one of the dashboard's dataset endpoints; refusals throw a `DatasetError`. */
export async function datasetCall<T>(
	url: string,
	init: { method?: 'GET' | 'POST' | 'PUT' | 'DELETE'; body?: unknown; signal?: AbortSignal } = {}
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

/** The snippet that writes a dataset into the org's flock-datasets (section 1 of 013). */
export const writeSnippet = (org: string) =>
	[
		'import lance',
		'from flock.hub import s3',
		'',
		`creds = s3.credentials("${org}")`,
		'lance.write_dataset(',
		`    table, creds.uri("${DATASETS_BUCKET}", "eval", "mmlu"), storage_options=creds.storage_options()`,
		')'
	].join('\n');

/** The snippet that opens a dataset with Lance, with a long-lived key filled in by hand. */
export function readSnippet(path: string, endpoint: string, region: string): string {
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
		`    "${datasetUri(path)}",`,
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
