import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { page as screen, userEvent } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../../routes/layout.css';
import { goto, resetApp } from '#lib/testing/app.svelte.ts';
import CommandPalette from './CommandPalette.svelte';

vi.mock('$app/navigation', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/state', () => import('#lib/testing/app.svelte.ts'));

const project = (name: string) => ({
	entity: 'acme',
	name,
	description: null,
	created_at: 1,
	updated_at: 2,
	run_count: 2,
	active_run_count: 0,
	column_count: 0,
	visibility: 'private',
	created_by: 7
});

/** Flock's answers: bert's groups and runs, as the palette asks for them (runs searched by name). */
function answer(url: string, init?: RequestInit): unknown {
	if (url.endsWith('/projects/acme/bert/groups'))
		return { groups: [{ id: 4, name: 'Seeds', color: '#36f' }], ungrouped_count: 0 };
	if (url.endsWith('/projects/acme/bert/runs/query')) {
		const body = JSON.stringify(JSON.parse(String(init?.body ?? '{}')).filter ?? '');
		const q = /\(\?i\)([\w-]+)/.exec(body)?.[1]?.toLowerCase() ?? '';
		const runs = [
			['r1', 'lr-sweep-1'],
			['r2', 'lr-sweep-2']
		].filter(([, n]) => n.includes(q));
		return {
			ids: runs.map(([id]) => id),
			data: { display_name: runs.map(([, n]) => n), job_type: runs.map(() => 'train') }
		};
	}
	return null;
}

beforeEach(async () => {
	await screen.viewport(1280, 800);
	resetApp('http://flock.test/acme', {
		shell: { access: 'member', org: 'acme', s3: false, projects: [project('bert')] }
	});
	vi.stubGlobal(
		'fetch',
		vi.fn(
			async (url: string, init?: RequestInit) =>
				new Response(JSON.stringify(answer(url, init)), { status: 200 })
		)
	);
});
afterEach(() => vi.unstubAllGlobals());

describe('the command menu', () => {
	it('shows a place opened before at once, while Flock is read again', async () => {
		render(CommandPalette, { open: true });
		await userEvent.keyboard('bert');
		await userEvent.keyboard('{Tab}');
		await expect.element(screen.getByRole('option', { name: /lr-sweep-2/ })).toBeVisible();
		await userEvent.keyboard('{Backspace}');

		// Flock stops answering: bert's runs are still there, as kept.
		vi.stubGlobal(
			'fetch',
			vi.fn(() => new Promise(() => {}))
		);
		await userEvent.keyboard('{Tab}');
		await expect.element(screen.getByRole('option', { name: /lr-sweep-2/ })).toBeVisible();
	});

	it('opens a project in place with Tab, goes back up with Backspace, and goes with Enter', async () => {
		render(CommandPalette, { open: true });
		const field = screen.getByRole('combobox', { name: 'Search or jump to' });
		await expect.element(field).toHaveFocus();

		await userEvent.keyboard('bert');
		await expect.element(screen.getByRole('option', { name: /bert/ })).toBeVisible();
		// Filtered: the places listed ahead of the projects are gone, so bert is the row Tab opens.
		await expect.element(screen.getByRole('option', { name: /^Home/ })).not.toBeInTheDocument();
		await userEvent.keyboard('{Tab}');

		// Inside: a chip for the project, its groups, then its runs.
		await expect.element(screen.getByText('bert ›')).toBeVisible();
		await expect.element(screen.getByRole('option', { name: /Seeds/ })).toBeVisible();
		await expect.element(screen.getByRole('option', { name: /lr-sweep-2/ })).toBeVisible();
		await expect.element(screen.getByText('Back')).toBeVisible();

		await userEvent.keyboard('{Backspace}');
		await expect.element(screen.getByText('bert ›')).not.toBeInTheDocument();
		await expect.element(screen.getByRole('option', { name: /^bert/ })).toBeVisible();

		await userEvent.keyboard('{Tab}');
		await expect.element(screen.getByRole('option', { name: /lr-sweep-1/ })).toBeVisible();
		await userEvent.keyboard('lr-sweep-2{Enter}');
		expect(goto).toHaveBeenLastCalledWith('/acme/bert/runs/r2');
	});

	it('opens a run in place with Tab, listing its pages', async () => {
		render(CommandPalette, { open: true });
		await expect.element(screen.getByRole('combobox', { name: 'Search or jump to' })).toHaveFocus();
		await userEvent.keyboard('bert');
		await expect.element(screen.getByRole('option', { name: /bert/ })).toBeVisible();
		// Filtered: the places listed ahead of the projects are gone, so bert is the row Tab opens.
		await expect.element(screen.getByRole('option', { name: /^Home/ })).not.toBeInTheDocument();
		await userEvent.keyboard('{Tab}');
		await expect.element(screen.getByRole('option', { name: /lr-sweep-1/ })).toBeVisible();
		await userEvent.keyboard('lr-sweep-2');
		await expect
			.element(screen.getByRole('option', { name: /lr-sweep-1/ }))
			.not.toBeInTheDocument();
		await userEvent.keyboard('{Tab}');

		await expect.element(screen.getByText('lr-sweep-2 ›')).toBeVisible();
		await expect.element(screen.getByRole('option', { name: /Charts/ })).toBeVisible();
		await expect.element(screen.getByRole('option', { name: /Logs/ })).toBeVisible();
		await expect.element(screen.getByRole('option', { name: /Files/ })).toBeVisible();
		// A training run has no Results or Compare.
		await expect.element(screen.getByRole('option', { name: /Results/ })).not.toBeInTheDocument();
		await userEvent.keyboard('logs{Enter}');
		expect(goto).toHaveBeenLastCalledWith('/acme/bert/runs/r2?tab=logs');
	});
});
