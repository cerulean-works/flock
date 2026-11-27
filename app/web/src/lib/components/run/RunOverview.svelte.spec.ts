import { describe, expect, it, vi } from 'vitest';
import { page as screen, userEvent } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../../routes/layout.css';
import type { RunDetail } from '#lib/types.ts';
import RunOverview from './RunOverview.svelte';

const RUN = {
	id: 'whrub7fm',
	entity: 'acme',
	project: 'pretrain',
	display_name: 'mix-w0.8',
	state: 'failed',
	created_at: 1_795_000_000,
	updated_at: 1_795_000_042,
	heartbeat_at: null,
	tags: ['v2'],
	group: null,
	job_type: 'pretrain',
	notes: 'died before the first step',
	description: null,
	host: 'gpu-047',
	program: 'pretrain.py',
	commit: '3ba901a4edbb34950b9e8f498c031b26ddf09bec',
	sweep: null,
	exitcode: 1,
	owner: null,
	gpu_type: 'H100',
	gpu_count: 8,
	gpu_detected: { type: 'H100', count: 8 },
	gpu_hours: 0.1,
	runtime: 42,
	cost: null,
	config: { lr: 0.0003, seed: 2, batch_tokens: 1048576 },
	summary: {},
	history_keys: [],
	system_keys: [],
	history_line_count: 0,
	system_line_count: 0,
	log_line_count: 3
} satisfies RunDetail;

describe('RunOverview', () => {
	it('leads a failed run with its exit code, the way to its logs and its notes', async () => {
		const onlogs = vi.fn();
		render(RunOverview, {
			run: RUN,
			runState: 'failed',
			owner: null,
			currency: null,
			now: 1_795_100_000,
			onlogs
		});
		await expect.element(screen.getByText('· exit 1')).toBeVisible();
		await screen.getByRole('button', { name: 'See logs' }).click();
		expect(onlogs).toHaveBeenCalledOnce();
		await expect.element(screen.getByText('Notes', { exact: true })).toBeVisible();
		// The commit is cut to 12 characters, the whole hash a copy away.
		await expect.element(screen.getByText('3ba901a4edbb', { exact: true })).toBeVisible();
		await expect
			.element(screen.getByRole('button', { name: 'Copy the commit' }))
			.toBeInTheDocument();
	});

	it('finds config keys from the search icon on the list row, Esc closes it', async () => {
		render(RunOverview, {
			run: { ...RUN, state: 'finished', exitcode: 0 },
			runState: 'finished',
			owner: null,
			currency: null,
			now: 1_795_100_000
		});
		await expect.element(screen.getByRole('button', { name: 'See logs' })).not.toBeInTheDocument();
		await screen.getByRole('button', { name: 'Search config keys' }).click();
		const field = screen.getByRole('searchbox', { name: 'Search config keys' });
		await expect.element(field).toHaveFocus();
		await userEvent.keyboard('se');
		await expect.element(screen.getByText('seed', { exact: true })).toBeVisible();
		await expect.element(screen.getByText('lr', { exact: true })).not.toBeInTheDocument();
		// Escape clears the text first, then closes the field.
		await userEvent.keyboard('{Escape}');
		await expect.element(field).toHaveValue('');
		await expect.element(screen.getByText('lr', { exact: true })).toBeVisible();
		await userEvent.keyboard('{Escape}');
		await expect.element(field).not.toBeInTheDocument();
	});
});
