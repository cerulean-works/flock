import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { page as screen } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../../layout.css';
import { noAxes } from '#lib/chart/axes.ts';
import { runWorkspace } from '#lib/data/run.ts';
import { columnDef } from '#lib/data/snapshot.ts';
import { page, refreshAll, resetApp } from '#lib/testing/app.svelte.ts';
import type { ProjectInfo, RunDetail } from '#lib/types.ts';
import Page from './+page.svelte';

vi.mock('$app/forms', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/navigation', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/state', () => import('#lib/testing/app.svelte.ts'));

/** The page's live stream, which only sends what a spec tells it to. */
class FakeSource {
	static last: FakeSource | null = null;
	listeners = new Map<string, (e: MessageEvent) => void>();
	onerror = null;
	constructor() {
		FakeSource.last = this;
	}
	addEventListener(name: string, listener: (e: MessageEvent) => void) {
		this.listeners.set(name, listener);
	}
	send(name: string, data: unknown) {
		this.listeners.get(name)?.(new MessageEvent(name, { data: JSON.stringify(data) }));
	}
	close() {}
}

const RUN = {
	id: 'abc123',
	entity: 'acme',
	project: 'bert',
	display_name: 'warm-sun-1',
	state: 'finished',
	created_at: 1_790_000_000,
	updated_at: 1_790_000_100,
	heartbeat_at: null,
	tags: [],
	group: null,
	job_type: null,
	notes: null,
	description: null,
	host: null,
	program: null,
	commit: null,
	sweep: null,
	exitcode: 0,
	owner: null,
	gpu_type: null,
	gpu_count: null,
	gpu_detected: { type: null, count: null },
	gpu_hours: 0,
	runtime: 100,
	cost: null,
	config: {},
	summary: {},
	history_keys: [],
	system_keys: [],
	history_line_count: 0,
	system_line_count: 0,
	log_line_count: 0
} satisfies RunDetail;

const project = (visibility: ProjectInfo['visibility']): ProjectInfo => ({
	entity: 'acme',
	name: 'bert',
	description: 'Masked language models',
	created_at: 1,
	updated_at: 2,
	run_count: 1,
	active_run_count: 0,
	column_count: 0,
	visibility,
	created_by: null
});

/** The project's page, public or not, seen with `access`. */
function open(visibility: ProjectInfo['visibility'], access: 'member' | 'public') {
	const { meta, page: runs } = runWorkspace(
		RUN,
		{ x: '_step', series: {} },
		{ x: '_runtime', series: {} },
		1_790_000_200
	);
	const data = {
		meta,
		runs,
		project: project(visibility),
		people: [],
		shell: { access, me: null, orgName: 'Acme', nameW: 280, projectView: 'table' }
	};
	resetApp('http://flock.test/acme/bert', data);
	page.params = { entity: 'acme', project: 'bert' };
	// @ts-expect-error -- a page's props: only what the workspace reads
	render(Page, { data });
}

beforeEach(() => {
	vi.stubGlobal('EventSource', FakeSource);
	refreshAll.mockClear();
});
afterEach(() => vi.unstubAllGlobals());

describe('a project’s header', () => {
	it('says a public project is public, to outsiders and members alike', async () => {
		for (const access of ['public', 'member'] as const) {
			open('public', access);
			const header = screen.getByRole('banner');
			await expect.element(header.getByRole('heading', { name: 'bert', level: 1 })).toBeVisible();
			await expect.element(header.getByText('Public', { exact: true })).toBeVisible();
			document.body.innerHTML = '';
		}
	});

	it('has no badge on a private project', async () => {
		open('private', 'member');
		await expect.element(screen.getByRole('heading', { name: 'bert', level: 1 })).toBeVisible();
		await expect.element(screen.getByText('Public', { exact: true })).not.toBeInTheDocument();
	});
});

describe('a public project made private', () => {
	it('is read again, which sends an outsider to sign in or to the 404 page', async () => {
		open('public', 'public');
		await expect.element(screen.getByRole('heading', { name: 'bert', level: 1 })).toBeVisible();
		await vi.waitFor(() => expect(FakeSource.last).not.toBeNull());
		FakeSource.last!.send('project', { visibility: 'private' });
		await vi.waitFor(() => expect(refreshAll).toHaveBeenCalled());
	});
});

/**
 * A project of eval runs as its snapshot gives them, one column per summary or config key, seen
 * with `access` on `url`: `zh` is an accuracy suite of task `t1`, `code` a bits-per-byte suite
 * of task `c1`, and `fp32` was scored under another dtype.
 */
function openEvals(access: 'member' | 'public', url: string, projectView = 'table') {
	const run = (zh: number, code: number, dtype = 'bfloat16') => ({
		'summary.suites/zh': zh,
		'summary.suites/code': code,
		'summary.t1/acc_raw': zh,
		'summary.t1/bpb': 2,
		'summary.c1/bpb': code,
		'config.suite_members.zh': ['t1'],
		'config.suite_members.code': ['c1'],
		'config.suite_hashes.zh': 'h-zh',
		'config.suite_hashes.code': 'h-code',
		'config.dtype': dtype
	});
	const runs = [
		{ id: 'r1', name: 'olmo', values: run(0.4, 0.9) },
		{ id: 'r2', name: 'llama', values: run(0.5, 0.8) },
		{ id: 'r3', name: 'fp32', values: run(0.9, 0.5, 'float32') }
	];
	const keys = Object.keys(runs[0].values);
	const numeric = (key: string) => typeof runs[0].values[key as 'summary.suites/zh'] === 'number';
	const valueOf = (r: (typeof runs)[number], key: string) => r.values[key as keyof typeof r.values];
	const data = {
		meta: {
			entity: 'acme',
			project: 'bert',
			pageSize: 100,
			now: 1_790_000_200,
			columns: keys.map((key) => columnDef(key, numeric(key) ? 'num' : 'json')),
			groups: [],
			metrics: [],
			panels: [],
			sections: []
		},
		runs: {
			query: { page: 1, sort: 'created_at', order: 'desc', size: 100, search: '', filters: [] },
			total: runs.length,
			projectTotal: runs.length,
			active: 0,
			runs: runs.map((r, ordinal) => ({
				id: r.id,
				name: r.name,
				state: 'finished',
				ordinal,
				lines: 0,
				systemLines: 0,
				defs: []
			})),
			metricCount: 0,
			num: keys.map((k) =>
				numeric(k) ? Float64Array.from(runs.map((r) => valueOf(r, k) as number)) : null
			),
			text: keys.map((k) => (numeric(k) ? null : runs.map((r) => valueOf(r, k)))),
			series: [],
			axes: runs.map(() => noAxes())
		},
		project: project('private'),
		people: [],
		shell: { access, me: null, orgName: 'Acme', nameW: 280, projectView }
	};
	resetApp(url, data);
	page.params = { entity: 'acme', project: 'bert' };
	// @ts-expect-error -- a page's props: only what the workspace reads
	render(Page, { data });
}

describe('the Evals preset', () => {
	it('is offered on the table of a project with suite scores, which it otherwise leaves as it is', async () => {
		openEvals('member', 'http://flock.test/acme/bert');
		const toggle = screen.getByRole('link', { name: 'Evals' });
		await expect.element(toggle).toHaveAttribute('href', '/acme/bert?preset=evals');
		await expect.element(toggle).toHaveAttribute('aria-pressed', 'false');
		await expect.element(screen.getByText('Mean rank (2)')).not.toBeInTheDocument();
	});

	it('ranks the runs scored alike by suite, each in its direction, and hides the others', async () => {
		openEvals('member', 'http://flock.test/acme/bert?preset=evals');
		const rows = screen.getByRole('row');
		await expect
			.element(screen.getByRole('link', { name: 'Evals' }))
			.toHaveAttribute('aria-pressed', 'true');
		await expect.element(screen.getByText('Mean rank (2)')).toBeVisible();
		// The header, then olmo and llama; fp32 waits behind the button.
		expect(rows.elements()).toHaveLength(3);
		await expect.element(rows.nth(1)).toHaveTextContent(/olmo.*2\.0.*40\.0%\s*2.*0\.900\s*2/);
		await expect.element(rows.nth(2)).toHaveTextContent(/llama.*1\.0.*50\.0%\s*1.*0\.800\s*1/);
		await expect.element(screen.getByRole('checkbox', { name: 'Select olmo' })).toBeVisible();

		await screen.getByRole('button', { name: 'Show 1 on other settings' }).click();
		await expect.element(rows.nth(3)).toHaveTextContent(/fp32.*Other settings.*90\.0%.*0\.500/);
		// Shown, never ranked: the FP32 run is first on both scores and has no mean rank.
		await expect.element(rows.nth(3)).toHaveTextContent(/Other settings\s*–/);
	});

	it('sorts by a mean rank, best first, then the other way', async () => {
		openEvals('member', 'http://flock.test/acme/bert?preset=evals');
		const names = () =>
			screen
				.getByRole('row')
				.elements()
				.slice(1)
				.map((row) => row.querySelector('a')?.textContent);
		await screen.getByRole('button', { name: 'Mean rank (2)' }).click();
		await vi.waitFor(() => expect(names()).toEqual(['llama', 'olmo']));
		await screen.getByRole('button', { name: 'Mean rank (2)' }).click();
		await vi.waitFor(() => expect(names()).toEqual(['olmo', 'llama']));
	});

	it('opens a link to the preset on the table, whichever view was picked last', async () => {
		openEvals('member', 'http://flock.test/acme/bert?preset=evals', 'charts');
		await expect.element(screen.getByText('Mean rank (2)')).toBeVisible();
		// Turned off, it goes back to the full table, not to the charts.
		await expect
			.element(screen.getByRole('link', { name: 'Evals' }))
			.toHaveAttribute('href', '/acme/bert?view=table');
	});

	it('shows outsiders of a public project the same scores, with nothing to select', async () => {
		openEvals('public', 'http://flock.test/acme/bert?preset=evals');
		await expect.element(screen.getByRole('link', { name: 'olmo' })).toBeVisible();
		await expect
			.element(screen.getByRole('checkbox', { name: 'Select olmo' }))
			.not.toBeInTheDocument();
	});
});
