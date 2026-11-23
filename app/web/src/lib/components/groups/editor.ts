import {
	MAX_RUNS,
	classifyColumns,
	configOf,
	displayKey,
	finite,
	keysOf,
	loadCall,
	runsQueryApi,
	type RunRow,
	type RunsResult
} from '#lib/groups.ts';

type Fetch = typeof fetch;

/** The run attributes the create and edit page show, beside the config. */
export const INFO_COLUMNS = ['display_name', 'state', 'created_at', 'group', 'tags', 'job_type'];

/**
 * What the create and edit page (D190) read: every run of the project (newest first, at most
 * `MAX_RUNS`) with its attributes and config, and the project's summary columns (a query of no
 * runs still names the columns a pattern matches). Summary values are read per column, for
 * the Best by column only (`runsQuery` with that column), as a project can log hundreds.
 */
export async function loadEditor(fetchFn: Fetch, entity: string, project: string) {
	const [runs, summary] = await Promise.all([
		loadCall<RunsResult>(fetchFn, runsQueryApi(entity, project), {
			method: 'POST',
			body: { columns: [...INFO_COLUMNS, 'config.*'], sort: ['-created_at'], limit: MAX_RUNS }
		}),
		loadCall<RunsResult>(fetchFn, runsQueryApi(entity, project), {
			method: 'POST',
			body: { columns: ['summary.*'], limit: 0 }
		}).catch(() => null)
	]);
	return { runs, summaryKeys: summary?.columns ?? [] };
}

/** What the assistant's empty thread offers when the project gives nothing better to say. */
export const FALLBACK_PROMPTS = [
	'Only finished runs',
	'Only batch sizes up to 128',
	'Leave out crashed runs'
] as const;

/**
 * Three requests the assistant's empty thread offers (D191), from the project's own runs where
 * they say enough: a state the runs are in, a config number that varies and a middle value of
 * it, and a run to add by hand (`addable`, the runs not in the draft). Each falls back to its
 * `FALLBACK_PROMPTS` entry.
 */
export function suggestPrompts(rows: readonly RunRow[], addable: readonly RunRow[]): string[] {
	const states = new Map<string, number>();
	for (const r of rows) if (r.state) states.set(r.state, (states.get(r.state) ?? 0) + 1);
	const state = states.has('finished')
		? 'finished'
		: [...states].sort((a, b) => b[1] - a[1])[0]?.[0];

	let numeric: string | null = null;
	for (const key of keysOf(classifyColumns(rows.map(configOf)), 'varies')) {
		const values = [...new Set(rows.map((r) => finite(r.cells[key])).filter((v) => v !== null))];
		if (values.length < 3) continue;
		values.sort((a, b) => a - b);
		numeric = `Only ${displayKey(key)} up to ${values[Math.floor((values.length - 1) / 2)]}`;
		break;
	}

	const run = addable.find((r) => r.name && r.name.length <= 40);
	return [
		state ? `Only ${state} runs` : FALLBACK_PROMPTS[0],
		numeric ?? FALLBACK_PROMPTS[1],
		run ? `Add ${run.name} too` : FALLBACK_PROMPTS[2]
	];
}
