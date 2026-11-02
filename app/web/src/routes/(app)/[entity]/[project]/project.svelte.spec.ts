import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { page as screen } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../../layout.css';
import { runWorkspace } from '#lib/data/run.ts';
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
	visibility
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
