/** The Storage tab (D63-D69): its URLs, object names and types, and previews. */

import { resolve } from '$app/paths';
import type { ResolvedPathname } from '$app/types';
import type { StorageObject } from './types.ts';

/** Entries per page of a folder, as the API serves them. */
export const PAGE = 200;
/** Above this size, uploads go in parts (S3 multipart), done by the browser. */
export const SINGLE_PUT_LIMIT = 100 * 1024 * 1024;
/** The largest file the dashboard uploads; larger ones are for the command line. */
export const UPLOAD_LIMIT = 50 * 1024 ** 3;
/** How much of a text object a preview reads. */
export const PREVIEW_BYTES = 1024 * 1024;

// ----------------------------------------------------------------------------------------------
// URLs: `/storage/{org}`, `/storage/{org}/{bucket}`, a folder (ending in `/`), an object.

const segments = (path: string) => path.split('/').map(encodeURIComponent).join('/');

/** The dashboard's endpoints for a bucket (`src/routes/api/storage`), which call Flock. */
export const bucketApi = (org: string, bucket: string, op: string) =>
	`/api/storage/${encodeURIComponent(org)}/${encodeURIComponent(bucket)}${op ? `/${op}` : ''}`;

/** The org's bucket overview. */
export const storageHref = (org: string) => resolve('/(app)/storage/[org]', { org });

/**
 * A folder of a bucket (`key` ends in `/`, or is empty for its root), or an object. Each part
 * of the key is encoded on its own, so its slashes stay and `#` or `?` in a name can't cut it.
 */
export const objectHref = (org: string, bucket: string, key = '') =>
	`${storageHref(org)}/${encodeURIComponent(bucket)}${key ? `/${segments(key)}` : ''}` as ResolvedPathname;

/** The key (a folder ending in `/`, or an object) a page's path under its bucket names. */
export function keyOfPath(rest: string, pathname: string): string {
	if (!rest) return '';
	return pathname.endsWith('/') && !rest.endsWith('/') ? `${rest}/` : rest;
}

/** One link of the breadcrumbs. */
export interface Crumb {
	label: string;
	href: ResolvedPathname;
}

/** Storage › org › bucket › folders… (the last one is the page itself). */
export function crumbs(org: string, orgLabel: string, bucket?: string, key = ''): Crumb[] {
	const out: Crumb[] = [
		{ label: 'Storage', href: storageHref(org) },
		{ label: orgLabel, href: storageHref(org) }
	];
	if (bucket === undefined) return out;
	out.push({ label: bucket, href: objectHref(org, bucket) });
	if (!key) return out;
	const parts = key.split('/');
	let prefix = '';
	for (const [i, part] of parts.entries()) {
		const last = i === parts.length - 1;
		if (last && !part) break; // after a folder's final /
		prefix += last ? part : `${part}/`;
		out.push({ label: part || '(empty)', href: objectHref(org, bucket, prefix) });
	}
	return out;
}

/** The folder an object or folder is in (`a/b/c.txt` → `a/b/`). */
export function parentOf(key: string): string {
	const trimmed = key.endsWith('/') ? key.slice(0, -1) : key;
	const at = trimmed.lastIndexOf('/');
	return at < 0 ? '' : trimmed.slice(0, at + 1);
}

/** What a key is called in its folder (`a/b/c.txt` → `c.txt`, `a/b/` → `b`). */
export function nameOf(key: string): string {
	const trimmed = key.endsWith('/') ? key.slice(0, -1) : key;
	return trimmed.slice(trimmed.lastIndexOf('/') + 1);
}

/** `s3://bucket/prefix`, as CLIs take it. */
export const s3Uri = (bucket: string, key = '') => `s3://${bucket}/${key}`;

// ----------------------------------------------------------------------------------------------
// Types and previews

const extension = (key: string) => {
	const name = nameOf(key);
	const dot = name.lastIndexOf('.');
	return dot > 0 ? name.slice(dot + 1).toLowerCase() : '';
};

const IMAGES: Record<string, string> = {
	png: 'image/png',
	jpg: 'image/jpeg',
	jpeg: 'image/jpeg',
	gif: 'image/gif',
	webp: 'image/webp',
	svg: 'image/svg+xml'
};
const VIDEO: Record<string, string> = {
	mp4: 'video/mp4',
	webm: 'video/webm',
	mov: 'video/quicktime'
};
const AUDIO: Record<string, string> = {
	mp3: 'audio/mpeg',
	wav: 'audio/wav',
	ogg: 'audio/ogg',
	flac: 'audio/flac',
	m4a: 'audio/mp4'
};
const TEXT = new Set(
	'txt log md markdown py ipynb yaml yml toml csv tsv jsonl ndjson ini cfg conf sh bash zsh js ts mjs cjs html htm css xml rs go c h cpp hpp java rb sql tex r jl lua env diff patch rst out err'.split(
		' '
	)
);

/** A short type for the listing's Type column, from the extension. */
export function typeLabel(key: string): string {
	if (key.endsWith('/')) return 'Folder';
	const ext = extension(key);
	if (!ext) return 'File';
	if (ext in IMAGES) return `${ext.toUpperCase()} image`;
	if (ext === 'json') return 'JSON';
	if (ext === 'pdf') return 'PDF';
	if (ext in VIDEO) return `${ext.toUpperCase()} video`;
	if (ext in AUDIO) return `${ext.toUpperCase()} audio`;
	if (TEXT.has(ext)) return ext === 'md' || ext === 'markdown' ? 'Markdown' : ext.toUpperCase();
	return ext.toUpperCase();
}

export type PreviewKind = 'image' | 'text' | 'markdown' | 'json' | 'video' | 'audio' | 'pdf' | null;

/** How an object is previewed (D67), and as which type the proxy should serve it inline. */
export interface Preview {
	kind: PreviewKind;
	/** For `image`, `video`, `audio` and `pdf`: the type asked of the proxy. */
	type?: string;
}

/** How to preview an object: by its content type, then by its extension. */
export function previewOf(key: string, contentType: string | null | undefined): Preview {
	const type = (contentType ?? '').split(';')[0].trim().toLowerCase();
	const ext = extension(key);
	if (type.startsWith('image/') && Object.values(IMAGES).includes(type))
		return { kind: 'image', type };
	if (type === 'application/pdf') return { kind: 'pdf', type };
	if (type === 'application/json') return { kind: 'json' };
	if (type === 'text/markdown') return { kind: 'markdown' };
	if (type.startsWith('video/')) return { kind: 'video', type };
	if (type.startsWith('audio/')) return { kind: 'audio', type };
	if (ext in IMAGES) return { kind: 'image', type: IMAGES[ext] };
	if (ext === 'pdf') return { kind: 'pdf', type: 'application/pdf' };
	if (ext === 'json') return { kind: 'json' };
	if (ext === 'md' || ext === 'markdown') return { kind: 'markdown' };
	if (ext in VIDEO) return { kind: 'video', type: VIDEO[ext] };
	if (ext in AUDIO) return { kind: 'audio', type: AUDIO[ext] };
	if (TEXT.has(ext) || type.startsWith('text/')) return { kind: 'text' };
	return { kind: null };
}

/** JSON text pretty-printed, or null if it doesn't parse (a cut-off file, JSON lines). */
export function prettyJson(text: string): string | null {
	try {
		return JSON.stringify(JSON.parse(text), null, 2);
	} catch {
		return null;
	}
}

// ----------------------------------------------------------------------------------------------
// The listing

/** A row of a folder's listing: a subfolder or an object. */
export type Entry = { kind: 'folder'; key: string } | ({ kind: 'object' } & StorageObject);

/** Folders first, then objects, as one list. */
export const entries = (folders: string[], objects: StorageObject[]): Entry[] => [
	...folders.map((key) => ({ kind: 'folder' as const, key })),
	...objects.map((o) => ({ kind: 'object' as const, ...o }))
];

/**
 * The keys selected after a click on `key` of `keys` (in listing order): toggled, or with shift
 * held, every key from the last click (`anchor`) to this one set as `key` now is.
 */
export function clickSelect(
	selected: ReadonlySet<string>,
	keys: readonly string[],
	key: string,
	anchor: string | null,
	shift: boolean
): Set<string> {
	const out = new Set(selected);
	const on = !selected.has(key);
	const from = anchor === null ? -1 : keys.indexOf(anchor);
	const to = keys.indexOf(key);
	if (shift && from >= 0 && to >= 0) {
		const [a, b] = from < to ? [from, to] : [to, from];
		for (const k of keys.slice(a, b + 1)) {
			if (on) out.add(k);
			else out.delete(k);
		}
	} else if (on) out.add(key);
	else out.delete(key);
	return out;
}

/** Why a folder name can't be used, or null. */
export function folderNameError(name: string): string | null {
	if (!name.trim()) return 'Enter a name';
	if (name.includes('/')) return 'Use a name without /: create one folder at a time';
	// eslint-disable-next-line no-control-regex
	if (/[\u0000-\u001f\u007f]/.test(name)) return 'Remove the control characters';
	if (new TextEncoder().encode(name).length > 512) return 'Use a shorter name';
	return null;
}

/** A request to one of the dashboard's bucket endpoints (a POST with `body`); refusals throw. */
export async function bucketCall<T>(
	org: string,
	bucket: string,
	op: string,
	body?: unknown
): Promise<T> {
	const init: RequestInit =
		body === undefined
			? {}
			: {
					method: 'POST',
					headers: { 'content-type': 'application/json' },
					body: JSON.stringify(body)
				};
	const res = await fetch(bucketApi(org, bucket, op), init);
	const json = await res.json().catch(() => null);
	if (!res.ok) throw new Error(json?.message ?? `Flock answered ${res.status}`);
	return json as T;
}
