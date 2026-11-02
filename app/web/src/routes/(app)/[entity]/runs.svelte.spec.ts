import { beforeEach, describe, expect, it, vi } from 'vitest';
import { page } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../layout.css';
import { app, resetApp } from '#lib/testing/app.svelte.ts';
import Page from './+page.svelte';

vi.mock('$app/forms', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/navigation', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/state', () => import('#lib/testing/app.svelte.ts'));

const HOST = 'https://flock-api.flock.test';
const KEY = 'flock-wandb-' + 'Z'.repeat(40);

/** An org where nothing has been logged yet. */
const data = {
	flockUrl: HOST,
	shell: { projects: [] },
	org: { name: 'acme', display_name: 'Acme', project_count: 0, member_count: 1 },
	feed: { items: [], next_cursor: null },
	query: { org: 'acme', kinds: 'run_started', limit: '50' },
	mine: false,
	project: null,
	now: 1_790_000_000
};

beforeEach(() => resetApp('http://flock.test/acme', data));

describe('the empty Runs page', () => {
	it('names the host once, in the step, and the new key shows only the key', async () => {
		app.result = { type: 'success', status: 200, data: { key: KEY, name: 'default' } };
		// @ts-expect-error -- a page's props: only what the empty state reads
		render(Page, { data, params: { entity: 'acme' } });
		await expect.element(page.getByRole('heading', { name: 'Log your first run' })).toBeVisible();
		await expect.element(page.getByText('Install the SDK')).toBeVisible();
		const step = page.getByRole('listitem').filter({ hasText: 'with this key' });
		await expect.element(step.getByText(`Log in to ${HOST} with this key`)).toBeVisible();

		await step.getByRole('button', { name: 'Create wandb key' }).click();
		await expect.element(step.getByLabelText('wandb key', { exact: true })).toHaveValue(KEY);
		await expect.element(step.getByRole('button', { name: 'Copy wandb key' })).toBeVisible();
		expect(app.sent.map((s) => s.action)).toEqual(['/account?/createKey']);

		// The host is in the step's text, and nowhere else; no command wraps the key.
		const text = document.body.innerText;
		expect(text.split(HOST).length).toBe(2);
		expect(text).not.toContain('wandb login');
		expect(text).not.toContain('WANDB_BASE_URL');
		await expect.element(page.getByText('Log a run to Acme')).toBeVisible();
	});
});

describe('the org’s page from outside it', () => {
	const BERT = {
		entity: 'acme',
		name: 'bert',
		description: 'Masked language models',
		created_at: 1_789_000_000,
		updated_at: 1_789_990_000,
		run_count: 12,
		active_run_count: 0,
		column_count: 0,
		visibility: 'public'
	};
	const outside = {
		flockUrl: HOST,
		shell: { projects: [BERT], access: 'public' },
		org: { name: 'acme', display_name: 'Acme', avatar_url: null, role: null },
		outside: true,
		datasets: [{ path: 'eval/mmlu', made_public_at: 1, version: 5, updated_at: 1_789_900_000 }],
		feed: null,
		query: {},
		mine: false,
		project: null,
		now: 1_790_000_000
	};

	it('lists its public projects and datasets, and nothing to change', async () => {
		resetApp('http://flock.test/acme', outside);
		// @ts-expect-error -- a page's props: only what the outsiders' view reads
		render(Page, { data: outside, params: { entity: 'acme' } });
		await expect.element(page.getByRole('heading', { name: 'Acme', level: 1 })).toBeVisible();

		const projects = page.getByRole('region', { name: /Projects/ });
		const bert = projects.getByRole('link', { name: 'bert' });
		await expect.element(bert).toBeVisible();
		expect(bert.element().getAttribute('href')).toBe('/acme/bert');
		await expect.element(projects.getByText('Masked language models')).toBeVisible();
		await expect.element(projects.getByText('12 runs')).toBeVisible();

		const datasets = page.getByRole('region', { name: /Datasets/ });
		const mmlu = datasets.getByRole('link', { name: 'eval/mmlu' });
		await expect.element(mmlu).toBeVisible();
		expect(mmlu.element().getAttribute('href')).toBe('/datasets/acme/eval/mmlu');
		await expect.element(datasets.getByText('v5')).toBeVisible();

		// No feed, no settings, no new project, no first-run instructions.
		await expect.element(page.getByText('Latest runs')).not.toBeInTheDocument();
		await expect.element(page.getByRole('link', { name: 'Settings' })).not.toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'New project' })).not.toBeInTheDocument();
		await expect.element(page.getByText('Log your first run')).not.toBeInTheDocument();
	});

	it('says when nothing of a kind is public', async () => {
		const empty = { ...outside, datasets: [] };
		resetApp('http://flock.test/acme', empty);
		// @ts-expect-error -- a page's props: only what the outsiders' view reads
		render(Page, { data: empty, params: { entity: 'acme' } });
		await expect.element(page.getByText('No public datasets.')).toBeVisible();
		await expect.element(page.getByRole('link', { name: 'bert' })).toBeVisible();
	});
});
