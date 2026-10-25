import type { DatasetVersion } from './types.ts';
import { bytes, count, formatNumber } from './utils.ts';

// Dataset pages: how rows' cells read, and what a version changed. Pure, so the server-rendered
// page and the hydrated one print the same text.

/** A cell's size when it is binary: Flock sends binary values as `{"$binary": size}`. */
export function binarySize(v: unknown): number | null {
	if (v === null || typeof v !== 'object' || Array.isArray(v)) return null;
	const keys = Object.keys(v);
	const size = (v as { $binary?: unknown }).$binary;
	return keys.length === 1 && typeof size === 'number' ? size : null;
}

export const binaryLabel = (size: number) => `binary · ${count(size, 'byte')}`;

/** Nested values on one line: JSON, with binary values by their size. */
export function compactJson(v: unknown): string {
	return JSON.stringify(v, (_, value: unknown) => {
		const size = binarySize(value);
		return size == null ? value : `‹${binaryLabel(size)}›`;
	});
}

export type CellKind = 'null' | 'number' | 'bool' | 'string' | 'binary' | 'json';

/** How a cell reads in the viewer: its text and what kind of value it is. */
export function formatCell(v: unknown): { text: string; kind: CellKind } {
	if (v === null || v === undefined) return { text: 'null', kind: 'null' };
	if (typeof v === 'number') return { text: formatNumber(v), kind: 'number' };
	if (typeof v === 'boolean') return { text: String(v), kind: 'bool' };
	if (typeof v === 'string') return { text: v, kind: 'string' };
	const size = binarySize(v);
	if (size != null) return { text: binaryLabel(size), kind: 'binary' };
	return { text: compactJson(v), kind: 'json' };
}

/** The cut cells of a page, by `row:col`, for quick lookups while rendering. */
export const truncatedCells = (truncated: readonly (readonly [number, number])[]) =>
	new Set(truncated.map(([r, c]) => `${r}:${c}`));

/** Arrow types whose values line up on the right. */
export const isNumericType = (type: string) =>
	/^(u?int(8|16|32|64)|float|double|halffloat|decimal)/.test(type);

/** An Arrow type as a column header shows it: `list<item: string>` reads `list<string>`. */
export const shortType = (type: string) => type.replace(/\b(item|element): /g, '');

const signed = (n: number, text: string) => `${n > 0 ? '+' : '−'}${text}`;

/**
 * What a version changed against the one before it (the next one in a newest-first list):
 * `+1,000 rows · +2.1 MB`. Empty when nothing is known or nothing changed.
 */
export function versionDelta(v: DatasetVersion, before: DatasetVersion | undefined): string {
	const parts: string[] = [];
	if (v.rows != null) {
		const d = v.rows - (before?.rows ?? 0);
		if (!before) parts.push(`${formatNumber(v.rows)} rows`);
		else if (d) parts.push(signed(d, `${formatNumber(Math.abs(d))} rows`));
	}
	if (v.bytes != null) {
		const d = v.bytes - (before?.bytes ?? 0);
		if (!before) parts.push(bytes(v.bytes));
		else if (d) parts.push(signed(d, bytes(Math.abs(d))));
	}
	return parts.join(' · ');
}

/** Lance's operation names as people read them: `UpdateConfig` is "update config". */
export function opLabel(op: string | null): string {
	if (!op) return 'internal';
	return op.replace(/([a-z])([A-Z])/g, '$1 $2').toLowerCase();
}

/** `flock/fineweb` for a dataset. */
export const repoId = (org: string, name: string) => `${org}/${name}`;

/** The Python snippet that loads a dataset with `flock.hub`. */
export function loadSnippet(org: string, name: string, version?: number | null): string {
	const args = [JSON.stringify(repoId(org, name)), version ? `version=${version}` : null];
	return `from flock.hub import load_dataset\n\nds = load_dataset(${args.filter(Boolean).join(', ')})`;
}

/** Reading a dataset with Lance itself, through the hub's S3 endpoint and an S3 key. */
export function lanceSnippet(
	uri: string,
	endpoint: string,
	region: string,
	version?: number | null
): string {
	return [
		'import lance',
		'',
		`ds = lance.dataset(`,
		`    ${JSON.stringify(uri)},`,
		...(version ? [`    version=${version},`] : []),
		'    storage_options={',
		`        "endpoint": ${JSON.stringify(endpoint)},`,
		`        "region": ${JSON.stringify(region)},`,
		'        "access_key_id": "<access key id>",',
		'        "secret_access_key": "<secret>",',
		'    },',
		')'
	].join('\n');
}

/** Listing a dataset's files with the AWS CLI. */
export const awsSnippet = (uri: string, endpoint: string) =>
	`aws s3 ls --recursive --endpoint-url ${endpoint} ${uri}/`;

/** Setting up the AWS CLI with an S3 key, as a profile named `flock`. */
export function awsConfigureSnippet(key: {
	access_key_id: string;
	secret_access_key: string;
	region: string;
	endpoint: string | null;
}): string {
	const lines = [
		`aws configure set aws_access_key_id ${key.access_key_id} --profile flock`,
		`aws configure set aws_secret_access_key ${key.secret_access_key} --profile flock`,
		`aws configure set region ${key.region} --profile flock`
	];
	if (key.endpoint) lines.push(`aws configure set endpoint_url ${key.endpoint} --profile flock`);
	return lines.join('\n');
}
