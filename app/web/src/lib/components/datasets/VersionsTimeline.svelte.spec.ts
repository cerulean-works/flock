import { describe, expect, it } from 'vitest';
import { page } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../../routes/layout.css';
import type { DatasetVersion, Role } from '#lib/types.ts';
import VersionsTimeline from './VersionsTimeline.svelte';

const version = (n: number): DatasetVersion => ({
	version: n,
	created_at: 1_700_000_000 + n,
	author: { id: 1, name: 'Ada', email: 'ada@flock.test', avatar_url: null },
	op: n === 1 ? 'Overwrite' : 'Append',
	message: `commit ${n}`,
	rows: 10 * n,
	bytes: 1000 * n,
	fragments: n,
	tags: [],
	pruned: false
});

const mount = (role: Role | null) =>
	render(VersionsTimeline, {
		versions: [version(2), version(1)],
		total: 2,
		latest: 2,
		current: 2,
		role,
		me: 1,
		now: 1_700_000_100,
		viewHref: (v: number) => `?v=${v}`
	});

describe('VersionsTimeline', () => {
	it('lets admins optimize and prune', async () => {
		mount('admin');
		await expect.element(page.getByRole('button', { name: 'Optimize' })).toBeVisible();
		await expect.element(page.getByRole('button', { name: 'Prune…' })).toBeVisible();
	});

	it('lets members optimize, but not prune', async () => {
		mount('member');
		await expect.element(page.getByRole('button', { name: 'Optimize' })).toBeVisible();
		await expect.element(page.getByRole('button', { name: 'Prune…' })).not.toBeInTheDocument();
	});

	it('offers outsiders neither', async () => {
		mount(null);
		await expect.element(page.getByText('commit 2')).toBeVisible();
		await expect.element(page.getByRole('button', { name: 'Optimize' })).not.toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Prune…' })).not.toBeInTheDocument();
	});
});
