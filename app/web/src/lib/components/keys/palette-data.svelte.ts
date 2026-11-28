/**
 * What the command menu reads from Flock, kept for the session rather than per opening: the
 * menu is unmounted each time ⌘K closes, and every read is a round trip (about 300ms on the
 * deployed server). The org's lists are read at idle once the app is up, so the first ⌘K can
 * already search them; places opened in the menu are kept, shown at once when opened again
 * and read again behind them.
 */
import { datasetCall, datasetsApi } from '#lib/datasets.ts';
import { folderApi } from '#lib/models.ts';
import type { Bucket, DatasetAt, DatasetFolder, ModelAt, ModelFolder } from '#lib/types.ts';

/** Read again when older than this, on the next ⌘K. */
const STALE_MS = 5 * 60_000;

export interface OrgData {
	org: string;
	/** The top dataset and model folders, and the live buckets. */
	tops: { datasets: string[]; models: string[]; buckets: string[] } | null;
	/** Every dataset and model in the first folders walked (Flock has no search across them). */
	deep: {
		datasets: { name: string; path: string; version: number | null }[];
		models: { name: string; primary: number | null }[];
	} | null;
	at: number;
}

export const orgData = $state<{ current: OrgData | null }>({ current: null });
let reading: string | null = null;

/** Reads the org's lists unless they were read lately (or are being read). */
export function warm(org: string) {
	const now = orgData.current;
	if (reading === org || (now?.org === org && Date.now() - now.at < STALE_MS)) return;
	reading = org;
	void read(org).finally(() => {
		if (reading === org) reading = null;
	});
}

async function read(o: string) {
	const getJson = async <T>(url: string): Promise<T> => {
		const res = await fetch(url);
		if (!res.ok) throw new Error(`Flock answered ${res.status}`);
		return (await res.json()) as T;
	};
	// The tops first: few reads, shown as soon as they're in.
	const [datasets, models, buckets] = await Promise.all([
		datasetCall<DatasetFolder | DatasetAt>(datasetsApi(o, 'at', { path: '' }))
			.then((at) => (at.kind === 'folder' ? at.folders.map((f) => f.path) : []))
			.catch(() => [] as string[]),
		datasetCall<ModelFolder | ModelAt>(folderApi(o, ''))
			.then((at) => (at.kind === 'folder' ? at.folders.map((f) => f.path) : []))
			.catch(() => [] as string[]),
		getJson<{ buckets: Bucket[] }>(`/api/storage/${encodeURIComponent(o)}`)
			.then((b) =>
				b.buckets
					.filter((x) => x.deleted_at === undefined || x.deleted_at === null)
					.map((x) => x.name)
			)
			.catch(() => [] as string[])
	]);
	const kept = orgData.current?.org === o ? orgData.current.deep : null;
	orgData.current = { org: o, tops: { datasets, models, buckets }, deep: kept, at: Date.now() };

	// Then the walk: the first 60 folders of each, two reads at a time (four in all).
	const deep: NonNullable<OrgData['deep']> = { datasets: [], models: [] };
	async function walk(read: (path: string) => Promise<string[]>) {
		const queue = [''];
		let budget = 60;
		const worker = async () => {
			while (queue.length && budget > 0) {
				budget--;
				queue.push(...(await read(queue.shift()!).catch(() => [])));
			}
		};
		await Promise.all([worker(), worker()]);
	}
	await Promise.all([
		walk(async (path) => {
			const at = await datasetCall<DatasetFolder | DatasetAt>(datasetsApi(o, 'at', { path }));
			if (at.kind !== 'folder') return [];
			for (const d of at.datasets) deep.datasets.push(d);
			return at.folders.map((f) => f.path);
		}),
		walk(async (path) => {
			const at = await datasetCall<ModelFolder | ModelAt>(folderApi(o, path));
			if (at.kind !== 'folder') return [];
			for (const m of at.models) deep.models.push(m);
			return at.folders.map((f) => f.path);
		})
	]);
	if (orgData.current?.org === o) orgData.current = { ...orgData.current, deep };
}

// ---- Places opened in the menu ------------------------------------------------------------

/** At most this many answers kept, the oldest let go first. */
const MAX = 300;
// Plain maps: nothing renders from them; the menu reads them as a place opens.
// eslint-disable-next-line svelte/prefer-svelte-reactivity -- read on demand, not rendered
const kept = new Map<string, unknown>();
// eslint-disable-next-line svelte/prefer-svelte-reactivity -- read on demand, not rendered
const inFlight = new Map<string, Promise<unknown>>();

/** An answer kept for `key`, freshest last. */
export function cached<T>(key: string): T | undefined {
	return kept.get(key) as T | undefined;
}

export function keep(key: string, value: unknown) {
	kept.delete(key);
	kept.set(key, value);
	if (kept.size > MAX) kept.delete(kept.keys().next().value!);
}

/** The read under way for `key`, if a prefetch started one. */
export const pending = <T>(key: string) => inFlight.get(key) as Promise<T> | undefined;

/**
 * Reads `key` ahead of its being opened (the row in the cursor), unless it's kept or already
 * being read. Not aborted: a read started is worth keeping.
 */
export function prefetch<T>(key: string, load: () => Promise<T>) {
	if (kept.has(key) || inFlight.has(key)) return;
	const p = load()
		.then((value) => {
			keep(key, value);
			return value;
		})
		.finally(() => inFlight.delete(key));
	p.catch(() => {});
	inFlight.set(key, p);
}
