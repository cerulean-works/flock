import { beforeEach, describe, expect, it, vi } from 'vitest';
import { page } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../routes/layout.css';
import { goto, page as appPage, resetApp } from '#lib/testing/app.svelte.ts';
import type { S3Key, WandbKey } from '#lib/types.ts';
import KeysSection from './KeysSection.svelte';

vi.mock('$app/forms', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/navigation', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/state', () => import('#lib/testing/app.svelte.ts'));

const NOW = 1_790_000_000;
const KEYS: WandbKey[] = [
	{ id: 1, name: 'cluster', prefix: 'flock-wandb-', created_at: NOW - 86_400 },
	// Made before wandb keys had their own prefix: listed like any other.
	{ id: 2, name: 'laptop', prefix: 'flock-0a1b2c', created_at: NOW - 30 * 86_400 }
];
const S3_KEY: S3Key = {
	id: 7,
	name: 'notebook',
	access_key_id: 'FLKAAAAAAAAAAAAAAAAA',
	org: 'acme',
	read_only: false,
	ephemeral: false,
	session_id: null,
	api_key_id: null,
	source: null,
	created_at: NOW - 3600,
	expires_at: null,
	last_used_at: null
};
const S3 = {
	endpoint: 'https://s3.flock.test',
	region: 'us-east-1',
	keys: [S3_KEY],
	orgs: [{ name: 'acme', display_name: 'Acme', s3_key_max_ttl: null }]
};
const props = { keys: KEYS, s3: S3, defaultOrg: 'acme', now: NOW };

const tab = (name: string) => page.getByRole('tab', { name, exact: true });

beforeEach(() => resetApp('http://flock.test/account'));

describe('the keys tabs', () => {
	it('shows the wandb keys first, with what they are for', async () => {
		render(KeysSection, props);
		await expect.element(page.getByRole('heading', { name: 'Keys' })).toBeVisible();
		await expect.element(tab('wandb')).toHaveAttribute('aria-selected', 'true');
		await expect.element(tab('S3')).toHaveAttribute('aria-selected', 'false');
		await expect.element(page.getByText(/^For the wandb SDK\. Use it as/)).toBeVisible();
		await expect.element(page.getByText('cluster')).toBeVisible();
		await expect.element(page.getByText('laptop')).toBeVisible();
		await expect.element(page.getByRole('button', { name: 'Delete key laptop' })).toBeVisible();
		await expect.element(page.getByRole('button', { name: 'Create wandb key' })).toBeVisible();
		await expect.element(page.getByText('notebook')).not.toBeVisible();
	});

	it('opens the tab the URL names', async () => {
		resetApp('http://flock.test/account?keys=s3');
		render(KeysSection, props);
		await expect.element(tab('S3')).toHaveAttribute('aria-selected', 'true');
		await expect.element(page.getByText('notebook')).toBeVisible();
		await expect.element(page.getByRole('button', { name: 'Create S3 key' })).toBeVisible();
		await expect.element(page.getByText('cluster')).not.toBeVisible();
	});

	it('falls back to wandb for a tab it doesn’t know', async () => {
		resetApp('http://flock.test/account?keys=gcs');
		render(KeysSection, props);
		await expect.element(tab('wandb')).toHaveAttribute('aria-selected', 'true');
	});

	it('puts the tab in the URL, leaving wandb out as the default', async () => {
		resetApp('http://flock.test/account?other=1');
		render(KeysSection, props);
		await tab('S3').click();
		await expect.element(page.getByText('notebook')).toBeVisible();
		expect(goto).toHaveBeenLastCalledWith('/account?other=1&keys=s3', {
			replace: true,
			reset: false
		});
		expect(appPage.url.search).toBe('?other=1&keys=s3');

		await tab('wandb').click();
		await expect.element(page.getByText('cluster')).toBeVisible();
		expect(goto).toHaveBeenLastCalledWith('/account?other=1', { replace: true, reset: false });
	});

	it('shows only wandb keys, without tabs, on a server without S3', async () => {
		resetApp('http://flock.test/account?keys=s3');
		render(KeysSection, { ...props, s3: null });
		await expect.element(page.getByRole('tablist')).not.toBeInTheDocument();
		await expect.element(page.getByText('cluster')).toBeVisible();
	});
});
