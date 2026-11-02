import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { page as screen } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../routes/layout.css';
import { runWorkspace } from '#lib/data/run.ts';
import { app, page, resetApp } from '#lib/testing/app.svelte.ts';
import WorkspaceHarness from '#lib/testing/WorkspaceHarness.svelte';
import type { ProjectInfo, RunDetail } from '#lib/types.ts';
import MoveRunsDialog from './MoveRunsDialog.svelte';
import ProjectDialog from './ProjectDialog.svelte';
import RunsTable from './RunsTable.svelte';

vi.mock('$app/forms', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/navigation', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/state', () => import('#lib/testing/app.svelte.ts'));

const project = (name: string, visibility: ProjectInfo['visibility']): ProjectInfo => ({
	entity: 'acme',
	name,
	description: null,
	created_at: 1,
	updated_at: 2,
	run_count: 3,
	active_run_count: 0,
	column_count: 0,
	visibility
});

/** The page's viewer: what they may do in acme. */
const viewing = (access: 'admin' | 'member' | 'public') =>
	resetApp('http://flock.test/acme/bert', { shell: { access, me: null } });

const sent = () => app.sent.map((s) => ({ action: s.action, ...Object.fromEntries(s.form) }));

afterEach(() => vi.unstubAllGlobals());

describe('a project’s visibility', () => {
	it('is set by admins in its settings, confirming before it goes public', async () => {
		viewing('admin');
		render(ProjectDialog, { open: true, entity: 'acme', project: project('bert', 'private') });
		const section = screen.getByRole('group', { name: 'Visibility' });
		await expect.element(section.getByRole('radio', { name: /Private/ })).toBeChecked();
		await section.getByRole('radio', { name: /Public/ }).click();
		await screen.getByRole('button', { name: 'Save' }).click();

		const confirm = screen.getByRole('alertdialog');
		await expect.element(confirm.getByText('Make bert public?')).toBeVisible();
		await expect
			.element(
				confirm.getByText(
					'Anyone with the link will be able to see every run in this project, including configs, files and logs. Check that none of them hold secrets.'
				)
			)
			.toBeVisible();
		expect(app.sent).toEqual([]);
		await confirm.getByRole('button', { name: 'Make public' }).click();
		await vi.waitFor(() =>
			expect(sent()).toEqual([
				{
					action: '/acme?/update',
					project: 'bert',
					from: 'list',
					name: 'bert',
					description: '',
					visibility: 'public'
				}
			])
		);
	});

	it('goes private without asking', async () => {
		viewing('admin');
		render(ProjectDialog, { open: true, entity: 'acme', project: project('bert', 'public') });
		const section = screen.getByRole('group', { name: 'Visibility' });
		await expect.element(section.getByRole('radio', { name: /Public/ })).toBeChecked();
		await section.getByRole('radio', { name: /Private/ }).click();
		await screen.getByRole('button', { name: 'Save' }).click();
		await vi.waitFor(() =>
			expect(sent()).toMatchObject([{ action: '/acme?/update', visibility: 'private' }])
		);
		await expect.element(screen.getByRole('alertdialog')).not.toBeInTheDocument();
	});

	it('isn’t offered to members', async () => {
		viewing('member');
		render(ProjectDialog, { open: true, entity: 'acme', project: project('bert', 'private') });
		await expect.element(screen.getByLabelText('Name')).toBeVisible();
		await expect.element(screen.getByRole('group', { name: 'Visibility' })).not.toBeInTheDocument();
	});
});

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

const workspace = () =>
	runWorkspace(RUN, { x: '_step', series: {} }, { x: '_runtime', series: {} }, 1_790_000_200);

describe('moving runs', () => {
	beforeEach(() => {
		viewing('member');
		vi.stubGlobal(
			'fetch',
			vi.fn(async () =>
				Response.json({
					projects: [project('bert', 'private'), project('gpt', 'public'), project('t5', 'private')]
				})
			)
		);
	});

	it('warns that runs moved into a public project become public', async () => {
		render(WorkspaceHarness, {
			props: {
				data: workspace(),
				component: MoveRunsDialog,
				dialog: { kind: 'move', ids: ['abc123'] }
			}
		});
		const warning = screen.getByText(/The runs you move will be public\./);
		await expect.element(screen.getByRole('dialog')).toBeVisible();
		await screen.getByLabelText('Project').click();
		await screen.getByRole('option', { name: /t5/ }).click();
		await expect.element(warning).not.toBeInTheDocument();
		await screen.getByLabelText('Project').click();
		await screen.getByRole('option', { name: /gpt/ }).click();
		await expect.element(warning).toBeVisible();
	});
});

describe('the runs table from outside the org', () => {
	const table = (access: 'member' | 'public') => {
		viewing(access);
		page.params = { entity: 'acme', project: 'bert' };
		render(WorkspaceHarness, {
			props: { data: workspace(), component: RunsTable, props: { mode: 'table', nameW: 280 } }
		});
	};

	it('offers no selection and no run actions', async () => {
		table('public');
		await expect.element(screen.getByRole('link', { name: 'warm-sun-1' })).toBeVisible();
		await expect.element(screen.getByRole('checkbox')).not.toBeInTheDocument();
		await expect
			.element(screen.getByRole('button', { name: 'Actions for warm-sun-1' }))
			.not.toBeInTheDocument();
	});

	it('offers both to members', async () => {
		table('member');
		await expect.element(screen.getByRole('checkbox', { name: 'Select warm-sun-1' })).toBeVisible();
		await expect
			.element(screen.getByRole('button', { name: 'Actions for warm-sun-1' }))
			.toBeInTheDocument();
	});
});
