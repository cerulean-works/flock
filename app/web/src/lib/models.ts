/** The Models tab (014): its URLs, the calls it makes, tags and the snippets it shows. */

import { resolve } from '$app/paths';
import type { ResolvedPathname } from '$app/types';
import { tagError as datasetTagError, pyString } from './datasets.ts';
import { type Scope, asScope, scopeQuery } from './scope.ts';
import { PREVIEW_BYTES, bucketCall, objectHref } from './storage.ts';
import type { Presigned } from './types.ts';
import { unreachable } from './uploads.svelte.ts';
import { projectHref } from './workspace/url.ts';

/** The org's managed bucket, as S3 clients name it. */
export const MODELS_BUCKET = 'flock-models';
/** How long a deleted model or version waits in the trash. */
export const TRASH_DAYS = 14;

// ----------------------------------------------------------------------------------------------
// URLs: `/models/{org}/{path…}` for global, `/{org}/{project}/models/{path…}` for a project's
// (D169); a folder or a model (the API decides).

const segments = (path: string) => path.split('/').map(encodeURIComponent).join('/');

/**
 * A scope's models (an org alone is its global scope), or a folder or model in it. Each part of
 * the path is encoded on its own.
 */
export function modelsHref(scope: Scope | string, path = '') {
	const { org, project } = asScope(scope);
	const root =
		project === null
			? resolve('/(app)/models/[org]/[...path]', { org, path: '' }).replace(/\/$/, '')
			: `${projectHref(org, project)}/models`;
	return `${root}${path ? `/${segments(path)}` : ''}` as ResolvedPathname;
}

/** A model at a version (a number or a tag), and optionally a tab. */
export function versionHref(
	scope: Scope | string,
	name: string,
	version: string | number,
	tab?: string
) {
	const search = new URLSearchParams({ version: String(version), ...(tab ? { tab } : {}) });
	return `${modelsHref(scope, name)}?${search}` as ResolvedPathname;
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

type Query = Record<string, string | number | boolean | null | undefined>;

/**
 * The dashboard's endpoint for one of an org's model routes (`op`; none for a folder's
 * listing), with its query and the scope's `project` (D163).
 */
export function modelsApi(scope: Scope | string, op: string, query: Query = {}): string {
	const { org } = asScope(scope);
	const search = new URLSearchParams();
	for (const [name, value] of Object.entries({ ...query, ...scopeQuery(scope) })) {
		if (value === null || value === undefined) continue;
		search.append(name, String(value));
	}
	const qs = search.toString();
	return `/api/models/${encodeURIComponent(org)}${op ? `/${op}` : ''}${qs ? `?${qs}` : ''}`;
}

/**
 * The endpoint of one model's route, `{path}/-/{op}` (D163), or `{path}/-` for the model
 * itself.
 */
export const modelApi = (scope: Scope | string, name: string, op = '', query: Query = {}) =>
	modelsApi(scope, `${segments(name)}/-${op ? `/${op}` : ''}`, query);

/** The endpoint of one level of a scope's models (D158), from `cursor` on. */
export const folderApi = (scope: Scope | string, folder: string, cursor: string | null = null) =>
	modelsApi(scope, '', { folder, cursor });

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
// Names (014, section 6) and tags (D98, D107)

const NAME = /^[a-z0-9][a-z0-9._-]*$/;

/** Why `name` can't be a model's path (D158: segments of D98's names, 255 characters), or null. */
export function modelNameError(name: string): string | null {
	if (!name) return 'Enter a name';
	if (name.length > 255) return 'Use at most 255 characters';
	const parts = name.split('/');
	if (parts.some((p) => p.length > 100)) return 'Use at most 100 characters between slashes';
	if (!parts.every((p) => NAME.test(p)))
		return 'Use lowercase letters, digits, dots, hyphens and underscores, each part starting with a letter or digit, with / between folders';
	return null;
}

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

/**
 * How refs name a model (D162): `acme/eval/olmo2-1b` when global, `pretrain:sweeps/lr-3e-4` in
 * a project, as `ModelVersion` prints it.
 */
export const modelRef = (scope: Scope | string, name: string) => {
	const { org, project } = asScope(scope);
	return project === null ? `${org}/${name}` : `${project}:${name}`;
};

/** `acme/olmo2-1b@12`, the name the delete dialogs ask for. */
export const versionRef = (scope: Scope | string, name: string, number: number) =>
	`${modelRef(scope, name)}@${number}`;

/**
 * The snippet that loads a model with `load_from_hub`: `ref` is `name@version` in a project,
 * which it names with `project=` (D162), else the global ref.
 */
export const loadSnippet = (ref: string, project: string | null = null) =>
	[
		'from flock.hub import models',
		'',
		`model = models.load_from_hub("${ref}"${project === null ? '' : `, project=${pyString(project)}`}, device="cuda")`
	].join('\n');

/** A word for the shell, quoted when it needs to be. */
const shellWord = (word: string) =>
	/^[\w.@/:+-]+$/.test(word) ? word : `'${word.replaceAll("'", `'\\''`)}'`;

/** The command that downloads a model, as `loadSnippet` names it. */
export const pullSnippet = (ref: string, project: string | null = null) =>
	`flock models pull ${ref}${project === null ? '' : ` --project ${shellWord(project)}`}`;

/** The snippet that saves a model into a scope (D164), for the empty listing. */
export function saveSnippet(scope: Scope | string, name = 'my-model') {
	const { org, project } = asScope(scope);
	return [
		'from flock.hub import models',
		'',
		project === null
			? `models.save_to_hub(model, "${org}/${name}")`
			: `models.save_to_hub(model, "${name}", project=${pyString(project)})`
	].join('\n');
}

/** The command that pushes a folder as a model into a scope. */
export const pushSnippet = (scope: Scope | string, name = 'my-model') => {
	const { org, project } = asScope(scope);
	return project === null
		? `flock models push ./model ${org}/${name}`
		: `flock models push ./model ${name} --project ${shellWord(project)}`;
};
