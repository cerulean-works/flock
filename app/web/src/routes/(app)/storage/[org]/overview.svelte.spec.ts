import { beforeEach, describe, expect, it, vi } from 'vitest';
import { page } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../../layout.css';
import { resetApp } from '#lib/testing/app.svelte.ts';
import type { Bucket } from '#lib/types.ts';
import Page from './+page.svelte';

vi.mock('$app/forms', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/navigation', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/state', () => import('#lib/testing/app.svelte.ts'));

const bucket = (name: string, managed: Bucket['managed'] = null): Bucket => ({
	name,
	upstream_name: `00001-${name}`,
	created_at: 1_790_000_000,
	created_by: managed ? null : { id: 1, name: 'Alice', email: 'alice@acme.test', avatar_url: null },
	managed
});

const data = {
	shell: { admin: true, orgName: 'Acme' },
	s3: { endpoint: 'https://s3.flock.test', region: 'us-east-1' },
	buckets: [
		bucket('checkpoints'),
		bucket('flock-datasets', 'datasets'),
		bucket('flock-models', 'models')
	],
	nameLimit: 57,
	now: 1_790_000_100
};

beforeEach(() => resetApp('http://flock.test/storage/acme', data));

describe('the Storage overview', () => {
	it('marks flock-datasets as managed, opening it in Datasets and never deleting it', async () => {
		// @ts-expect-error -- a page's props: only what the overview reads
		render(Page, { data, params: { org: 'acme' } });
		const managed = page.getByRole('row').filter({ hasText: 'flock-datasets' });
		await expect.element(managed.getByText('Managed by Flock')).toBeVisible();
		const other = page.getByRole('row').filter({ hasText: 'checkpoints' });
		await expect.element(other.getByText('Managed by Flock')).not.toBeInTheDocument();

		await page.getByRole('button', { name: 'Actions for flock-datasets' }).click();
		const open = page.getByRole('menuitem', { name: 'Open in Datasets' });
		await expect.element(open).toBeVisible();
		expect(open.element().getAttribute('href')).toBe('/datasets/acme');
		await expect.element(page.getByRole('menuitem', { name: 'History' })).toBeVisible();
		await expect
			.element(page.getByRole('menuitem', { name: 'Delete bucket…' }))
			.not.toBeInTheDocument();
	});

	it('marks flock-models as managed, opening it in Models', async () => {
		// @ts-expect-error -- a page's props: only what the overview reads
		render(Page, { data, params: { org: 'acme' } });
		const managed = page.getByRole('row').filter({ hasText: 'flock-models' });
		await expect.element(managed.getByText('Managed by Flock')).toBeVisible();
		await page.getByRole('button', { name: 'Actions for flock-models' }).click();
		const open = page.getByRole('menuitem', { name: 'Open in Models' });
		await expect.element(open).toBeVisible();
		expect(open.element().getAttribute('href')).toBe('/models/acme');
		await expect
			.element(page.getByRole('menuitem', { name: 'Open in Datasets' }))
			.not.toBeInTheDocument();
		await expect
			.element(page.getByRole('menuitem', { name: 'Delete bucket…' }))
			.not.toBeInTheDocument();
	});

	it('still lets admins delete their own buckets', async () => {
		// @ts-expect-error -- a page's props: only what the overview reads
		render(Page, { data, params: { org: 'acme' } });
		await page.getByRole('button', { name: 'Actions for checkpoints' }).click();
		await expect.element(page.getByRole('menuitem', { name: 'Delete bucket…' })).toBeVisible();
		await expect
			.element(page.getByRole('menuitem', { name: 'Open in Datasets' }))
			.not.toBeInTheDocument();
	});
});
