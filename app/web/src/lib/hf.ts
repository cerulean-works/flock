/** Hugging Face imports from the web app (D124): the dashboard's endpoints, the defaults the
 * dialogs fill in, and how an import reads in its row. */

import { nameOf, parentOf } from './datasets.ts';
import type { HFDatasetPreview, HFImport } from './types.ts';
import { bytes } from './utils.ts';

/** The dashboard's endpoint for the org's imports: the list (no `op`), `preview` or one import. */
export function hfApi(
	org: string,
	op = '',
	query: Record<string, string | null | undefined> = {}
): string {
	const search = new URLSearchParams();
	for (const [name, value] of Object.entries(query)) {
		if (value !== null && value !== undefined) search.append(name, value);
	}
	const qs = search.toString();
	return `/api/imports/${encodeURIComponent(org)}/hf${op ? `/${op}` : ''}${qs ? `?${qs}` : ''}`;
}

const REPO = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;

/** Why `repo` isn't a Hub repository id, or null. */
export function repoError(repo: string): string | null {
	if (!repo) return 'Enter a repository';
	if (!REPO.test(repo) || repo.split('/').some((p) => p === '.' || p === '..'))
		return 'Enter the repository as owner/name';
	return null;
}

/** Where a dataset lands by default: the repository's name, in the folder shown. */
export const defaultFolder = (folder: string, repo: string) => {
	const name = nameOf(repo.trim());
	return folder ? `${folder}/${name}` : name;
};

/** A model's default name: the repository's, as a model name may be (lowercase). */
export function defaultModelName(repo: string): string {
	return nameOf(repo.trim())
		.toLowerCase()
		.replace(/[^a-z0-9._-]+/g, '-')
		.replace(/^[^a-z0-9]+/, '')
		.slice(0, 100);
}

/** A shell word, quoted when it needs to be (as Python's `shlex.join` quotes it). */
const quote = (word: string) =>
	/^[\w@%+=:,./-]+$/.test(word) ? word : `'${word.replaceAll("'", `'"'"'`)}'`;

/** The splits checked in the dataset dialog: each checked config with its checked splits, or
 * null for a config whose splits aren't known (all of them). */
export type SplitChoice = Record<string, string[] | null>;

/** A dataset's configs, as its preview lists them. */
type Configs = HFDatasetPreview['configs'];

/** What the dialog sends as `configs`: each chosen config, in the preview's order, with its
 * splits in order, or null when every split is chosen. */
export function chosenConfigs(
	configs: Configs,
	choice: SplitChoice
): Record<string, string[] | null> {
	const out: Record<string, string[] | null> = {};
	for (const config of configs) {
		if (!(config.name in choice)) continue;
		const picked = choice[config.name];
		const names = config.splits?.map((s) => s.name) ?? null;
		out[config.name] =
			picked === null || !names || names.every((n) => picked.includes(n))
				? null
				: names.filter((n) => picked.includes(n));
	}
	return out;
}

/** The bytes the chosen splits take, as far as the preview knows. */
export function chosenSize(configs: Configs, choice: SplitChoice): number {
	let total = 0;
	for (const config of configs) {
		const picked = choice[config.name];
		if (picked === undefined) continue;
		if (picked === null || !config.splits) total += config.size ?? 0;
		else for (const s of config.splits) if (picked.includes(s.name)) total += s.size ?? 0;
	}
	return total;
}

/** The `flock datasets import` command for what the dataset dialog asks, as the API writes it.
 * Configs whose splits differ need a command each, one per line. */
export function datasetCommand(
	org: string,
	repo: string,
	dest: string,
	configs: Record<string, string[] | null>
): string {
	const groups = new Map<string, { names: string[]; splits: string[] | null }>();
	for (const [name, splits] of Object.entries(configs)) {
		const key = JSON.stringify(splits);
		const group = groups.get(key) ?? { names: [], splits };
		group.names.push(name);
		groups.set(key, group);
	}
	return [...groups.values()]
		.map(({ names, splits }) => {
			const words = ['flock', 'datasets', 'import', repo, dest];
			for (const name of names) words.push('--config', name);
			for (const split of splits ?? []) words.push('--split', split);
			words.push('--org', org);
			return words.map(quote).join(' ');
		})
		.join('\n');
}

/** The imports a page shows: a dataset's in the folder it lands in, every model's. */
export function importsAt(imports: HFImport[], kind: 'dataset' | 'model', folder = '') {
	return imports.filter(
		(i) => i.kind === kind && (kind === 'model' || parentOf(i.dest) === folder)
	);
}

/** What a running import is doing, for its row. */
export function progressText(job: HFImport): string {
	const done = `${bytes(job.bytes_done)} of ${bytes(job.bytes_total)}`;
	switch (job.phase) {
		case 'downloading':
			return `Downloading. ${done}`;
		case 'converting':
			return `Converting to Lance. ${job.files_done} of ${job.files_total} files`;
		case 'uploading':
			return `Uploading. ${done}`;
		case 'committing':
			return 'Checking the files';
		default:
			return 'Starting';
	}
}

/** How an import that ended reads, for its row. */
export function endedText(job: HFImport): string {
	switch (job.state) {
		case 'finished':
			return job.result?.existing
				? `${job.repo} was already imported as version ${job.result.version}.`
				: `Imported ${job.repo}.`;
		case 'failed':
			return `The import of ${job.repo} failed.`;
		case 'cancelled':
			return `The import of ${job.repo} was cancelled.`;
		case 'interrupted':
			return `The import of ${job.repo} stopped when the server restarted. Start it again.`;
		default:
			return `Importing ${job.repo}`;
	}
}

/** Flock's answer to a refused import call: its message, and the `command` to run instead. */
export class HFError extends Error {
	status: number;
	body: Record<string, unknown>;

	constructor(status: number, body: Record<string, unknown>) {
		super(String(body.message ?? `Flock answered ${status}`));
		this.status = status;
		this.body = body;
	}

	/** The `flock` command to run instead, when the import is too large for the server. */
	get command(): string | null {
		return typeof this.body.command === 'string' ? this.body.command : null;
	}
}

/** A call to the dashboard's import endpoints, with the access token (if any) in a header. */
export async function hfCall<T>(
	url: string,
	init: { method?: 'GET' | 'POST' | 'DELETE'; body?: unknown; token?: string } = {}
): Promise<T> {
	const headers: Record<string, string> = {};
	if (init.body !== undefined) headers['content-type'] = 'application/json';
	if (init.token) headers['x-hf-token'] = init.token;
	const res = await fetch(url, {
		method: init.method ?? 'GET',
		headers,
		body: init.body === undefined ? undefined : JSON.stringify(init.body)
	});
	const json = await res.json().catch(() => null);
	if (!res.ok) throw new HFError(res.status, (json ?? {}) as Record<string, unknown>);
	return json as T;
}
