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
