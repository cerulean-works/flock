/** The Models tab (014): its URLs, the calls it makes, tags and the snippets it shows. */

import { resolve } from '$app/paths';
import type { ResolvedPathname } from '$app/types';
import { tagError as datasetTagError } from './datasets.ts';
import { PREVIEW_BYTES, bucketCall, objectHref } from './storage.ts';
import type { ModelEvalRun, Presigned } from './types.ts';
import { unreachable } from './uploads.svelte.ts';

/** The org's managed bucket, as S3 clients name it. */
export const MODELS_BUCKET = 'flock-models';
/** How long a deleted model or version waits in the trash. */
export const TRASH_DAYS = 14;

// ----------------------------------------------------------------------------------------------
// URLs: `/models/{org}` and `/models/{org}/{name}`.

/** The org's models, or one model. */
export const modelsHref = (org: string, name?: string) =>
	name === undefined
		? resolve('/(app)/models/[org]', { org })
		: resolve('/(app)/models/[org]/[name]', { org, name });

/** A model at a version (a number or a tag), and optionally a tab. */
export function versionHref(org: string, name: string, version: string | number, tab?: string) {
	const search = new URLSearchParams({ version: String(version), ...(tab ? { tab } : {}) });
	return `${modelsHref(org, name)}?${search}` as ResolvedPathname;
}

/** A version's folder in flock-models, in the Storage tab. */
export const folderHref = (org: string, prefix: string) => objectHref(org, MODELS_BUCKET, prefix);

/** A run's page, from its `entity/project/id`. */
export function runPathHref(path: string): ResolvedPathname | null {
	const [entity, project, run, ...rest] = path.split('/');
	if (!entity || !project || !run || rest.length) return null;
	return resolve('/(app)/[entity]/[project]/runs/[run]', { entity, project, run });
}

// ----------------------------------------------------------------------------------------------
// The dashboard's endpoints (`src/routes/api/models`), which call Flock as the signed-in user.
// They answer as the dataset endpoints do, so the same call and error serve both.

export { DatasetError as ModelError, datasetCall as modelCall } from './datasets.ts';

/** The dashboard's endpoint for one of an org's model routes, with its query. */
export function modelsApi(
	org: string,
	op: string,
	query: Record<string, string | number | boolean | null | undefined> = {}
): string {
	const search = new URLSearchParams();
	for (const [name, value] of Object.entries(query)) {
		if (value === null || value === undefined) continue;
		search.append(name, String(value));
	}
	const qs = search.toString();
	return `/api/models/${encodeURIComponent(org)}/${op}${qs ? `?${qs}` : ''}`;
}

/** The endpoint of one model's route (`op` after its name, or none for the model itself). */
export const modelApi = (
	org: string,
	name: string,
	op = '',
	query: Record<string, string | number | boolean | null | undefined> = {}
) => modelsApi(org, `${encodeURIComponent(name)}${op ? `/${op}` : ''}`, query);

/**
 * The text of a file of a version (`key` in flock-models), its first MiB, straight from the S3
 * proxy by a presigned URL, as the Storage tab's previews read it.
 */
export async function readText(org: string, key: string): Promise<string> {
	const signed = await bucketCall<Presigned>(org, MODELS_BUCKET, 'presign', {
		method: 'get',
		key,
		disposition: 'inline'
	});
	let res: Response;
	try {
		res = await fetch(signed.url!, { headers: { range: `bytes=0-${PREVIEW_BYTES - 1}` } });
	} catch {
		throw new Error(unreachable(signed.url!));
	}
	if (!res.ok) throw new Error(`The S3 proxy answered ${res.status}`);
	return res.text();
}

// ----------------------------------------------------------------------------------------------
// Tags (D98, D107)

const CHECKPOINT = /^checkpoint-(\d+)$/;

/** Whether a tag is a training checkpoint's, `checkpoint-{step}`. */
export const isCheckpoint = (tag: string) => CHECKPOINT.test(tag);

/**
 * Tags in the order the picker and the Versions tab show them: other tags by name, then
 * checkpoint tags by their step, so `checkpoint-900` comes before `checkpoint-12000`.
 */
export function sortTags<T extends string | { name: string }>(tags: readonly T[]): T[] {
	const name = (t: T) => (typeof t === 'string' ? t : t.name);
	const step = (t: T) => {
		const m = CHECKPOINT.exec(name(t));
		return m ? Number(m[1]) : null;
	};
	return [...tags].sort((a, b) => {
		const sa = step(a);
		const sb = step(b);
		if (sa === null && sb === null) return name(a).localeCompare(name(b));
		if (sa === null) return -1;
		if (sb === null) return 1;
		return sa - sb;
	});
}

/** Why a tag name can't be used on a model (D96's rules, and `primary`), or null. */
export function tagError(name: string): string | null {
	if (name.toLowerCase() === 'primary')
		return 'That name is for the primary version, so it can’t be a tag';
	return datasetTagError(name);
}

// ----------------------------------------------------------------------------------------------
// Display

/** `acme/olmo2-1b@12`, the name the delete dialogs ask for. */
export const versionRef = (org: string, name: string, number: number) => `${org}/${name}@${number}`;

/** The snippet that loads the version shown with `load_from_hub`. */
export const loadSnippet = (ref: string) =>
	[
		'from flock.hub import models',
		'',
		`model = models.load_from_hub("${ref}", device="cuda")`
	].join('\n');

/** The command that downloads the version shown. */
export const pullSnippet = (ref: string) => `flock models pull ${ref}`;

/**
 * The rows of the Evals tab: every numeric summary key the runs have, except wandb's own and
 * throughput, sorted by name so a task's metrics sit together.
 */
export function evalMetrics(runs: ModelEvalRun[]): string[] {
	const keys = new Set<string>();
	for (const run of runs)
		for (const key of Object.keys(run.metrics))
			if (!key.startsWith('_') && !key.endsWith('/docs_per_second')) keys.add(key);
	return [...keys].sort((a, b) => a.localeCompare(b));
}
