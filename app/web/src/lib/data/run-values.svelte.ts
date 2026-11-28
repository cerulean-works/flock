import { SvelteMap } from 'svelte/reactivity';
import { projectUrl } from '#lib/data/cache.ts';
import type { FilterValue as ColumnValue } from '#lib/components/ColumnFilter.svelte';
import type { ColumnKind, Owner } from '#lib/types.ts';

/** Per column, each value its runs have and how many have it, most first. */
export type RunValues = Record<string, { value: string | number; runs: number }[]>;

const loaded = new SvelteMap<string, RunValues>();
// Not reactive: asking happens while deriving what a header shows.
let asked: Record<string, true> = {};

/** Flock counts at most this many columns' values a request: more are asked in turns. */
const KEYS_PER_REQUEST = 32;

/**
 * The values of `keys` among a project's runs (or a group's), with counts: what each column's
 * header filters to. Read once per page visit and kept; until then, nothing.
 */
export function runValues(
	entity: string,
	project: string,
	keys: readonly string[],
	group?: number | null
): RunValues {
	if (!keys.length) return {};
	const base = projectUrl(entity, project);
	const from = group == null ? `${base}/runs/values` : `${base}/groups/${group}/runs/values`;
	// A project with many config columns lists more than Flock counts at once; the rest
	// (owner among them, sorted after config.*) would come back empty.
	const sorted = [...keys].sort();
	if (sorted.length <= KEYS_PER_REQUEST) return read(from, sorted);
	const out: RunValues = {};
	for (let i = 0; i < sorted.length; i += KEYS_PER_REQUEST)
		Object.assign(out, read(from, sorted.slice(i, i + KEYS_PER_REQUEST)));
	return out;
}

function read(from: string, keys: readonly string[]): RunValues {
	const url = `${from}?keys=${encodeURIComponent(keys.join(','))}`;
	if (!asked[url] && typeof window !== 'undefined') {
		asked[url] = true;
		fetch(url)
			.then((r) => (r.ok ? (r.json() as Promise<{ values: RunValues }>) : null))
			.then((body) => body && loaded.set(url, body.values))
			.catch(() => delete asked[url]);
	}
	return loaded.get(url) ?? {};
}

/** Forget what was read, so the next look reads again (runs were changed). */
export function forgetRunValues() {
	asked = {};
	loaded.clear();
}

export const RUN_STATES = ['running', 'preempting', 'finished', 'failed', 'crashed'] as const;

/** A state's dot, as StateBadge draws it. */
export const STATE_DOT: Record<string, string> = {
	running: 'bg-state-running',
	preempting: 'bg-state-preempting',
	finished: 'bg-state-finished',
	crashed: 'bg-state-failed',
	failed: 'bg-state-failed'
};

/**
 * A listed column's choices as its filter shows them: every state with its dot, people by
 * name with their face (the viewer first, as Me), the rest as they read.
 */
export function columnChoices(
	kind: ColumnKind | undefined,
	counted: RunValues[string] | undefined,
	people: ReadonlyMap<number, Owner>,
	me?: number
): ColumnValue[] {
	const found = counted ?? [];
	if (kind === 'state') {
		const runs = (s: string) => found.find((f) => f.value === s)?.runs ?? 0;
		return RUN_STATES.map((s) => ({
			value: s,
			label: s[0].toUpperCase() + s.slice(1),
			count: runs(s),
			dot: STATE_DOT[s]
		}));
	}
	const out = found.map((f) => {
		const value = String(f.value);
		if (kind !== 'user') return { value, label: value, count: f.runs };
		const who = people.get(Number(f.value));
		return {
			value,
			label: Number(f.value) === me ? 'Me' : (who?.name ?? `user ${value}`),
			count: f.runs,
			person: who ? { name: who.name, avatar_url: who.avatar_url } : undefined
		};
	});
	if (kind === 'user' && me != null) {
		const i = out.findIndex((o) => o.value === String(me));
		if (i > 0) out.unshift(...out.splice(i, 1));
	}
	return out;
}
