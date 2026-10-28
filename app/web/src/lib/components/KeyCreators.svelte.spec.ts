import { beforeEach, describe, expect, it, vi } from 'vitest';
import { page } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../routes/layout.css';
import { app, resetApp } from '#lib/testing/app.svelte.ts';
import type { S3KeyCreated } from '#lib/types.ts';
import S3KeyCreator from './S3KeyCreator.svelte';
import WandbKeyCreator from './WandbKeyCreator.svelte';

vi.mock('$app/forms', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/navigation', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/state', () => import('#lib/testing/app.svelte.ts'));

beforeEach(() => resetApp('http://flock.test/account', { flockUrl: 'https://flock.test' }));

/** Everything the creator shows, inputs' values included. */
const shown = () =>
	document.body.innerText +
	[...document.querySelectorAll('input')].map((i) => `\n${i.value}`).join('');

describe('a new wandb key', () => {
	// Any format: the key is shown as the server gives it, never parsed.
	it.each(['flock-wandb-' + 'a'.repeat(40), 'flock-' + '0'.repeat(48), 'some-future-format'])(
		'shows only the key %s, with a copy button',
		async (key) => {
			app.result = { type: 'success', status: 200, data: { key, name: 'cluster' } };
			render(WandbKeyCreator, { named: true });
			await page.getByLabelText('Key name').fill('cluster');
			await page.getByRole('button', { name: 'Create wandb key' }).click();

			await expect.element(page.getByLabelText('wandb key', { exact: true })).toHaveValue(key);
			await expect.element(page.getByRole('button', { name: 'Copy wandb key' })).toBeVisible();
			await expect.element(page.getByText('Copy it now. It won’t be shown again.')).toBeVisible();
			expect(app.sent.map((s) => [s.action, s.form.get('name')])).toEqual([
				['/account?/createKey', 'cluster']
			]);
			// The secret alone: no command around it, and no host.
			const text = shown();
			expect(text).not.toContain('wandb login');
			expect(text).not.toContain('WANDB_BASE_URL');
			expect(text).not.toContain('flock.test');
			expect(text.split(key).length).toBe(2);
		}
	);

	it('shows a refusal in the form', async () => {
		app.result = { type: 'failure', status: 403, data: { error: 'not allowed' } };
		render(WandbKeyCreator);
		await page.getByRole('button', { name: 'Create wandb key' }).click();
		await expect.element(page.getByText('not allowed')).toBeVisible();
		await expect.element(page.getByLabelText('wandb key', { exact: true })).not.toBeInTheDocument();
	});
});

describe('a new S3 key', () => {
	const created: S3KeyCreated = {
		id: 4,
		name: 'notebook',
		access_key_id: 'FLKAAAAAAAAAAAAAAAAA',
		secret_access_key: 's3cr3t-s3cr3t',
		endpoint: 'https://s3.flock.test',
		region: 'us-east-1',
		org: 'acme',
		read_only: true,
		ephemeral: false,
		session_id: null,
		api_key_id: null,
		source: null,
		created_at: 1_790_000_000,
		expires_at: null,
		last_used_at: null
	};

	it('shows the ID and the secret to copy, and the endpoint, without snippets', async () => {
		app.result = { type: 'success', status: 200, data: { s3Key: created } };
		const orgs = [{ name: 'acme', display_name: 'Acme', s3_key_max_ttl: null }];
		render(S3KeyCreator, { orgs, initialOrg: 'acme' });
		await page.getByLabelText('Name').fill('notebook');
		await page.getByRole('button', { name: 'Create S3 key' }).click();

		await expect
			.element(page.getByLabelText('Access key ID', { exact: true }))
			.toHaveValue(created.access_key_id);
		await expect
			.element(page.getByLabelText('Secret access key', { exact: true }))
			.toHaveValue(created.secret_access_key);
		await expect
			.element(page.getByLabelText('Endpoint', { exact: true }))
			.toHaveValue(created.endpoint);
		await expect
			.element(page.getByLabelText('Endpoint', { exact: true }))
			.toHaveAttribute('readonly');
		await expect.element(page.getByRole('button', { name: 'Copy access key ID' })).toBeVisible();
		await expect
			.element(page.getByRole('button', { name: 'Copy secret access key' }))
			.toBeVisible();
		// Only those two: the endpoint is a plain field.
		expect(page.getByRole('button', { name: /^Copy/ }).all()).toHaveLength(2);
		await expect
			.element(page.getByText('Copy the secret now. It won’t be shown again.'))
			.toBeVisible();

		const text = shown();
		for (const snippet of ['aws_access_key_id', 'boto3', 'export AWS_', 'addressing_style'])
			expect(text).not.toContain(snippet);
		expect(text.split(created.secret_access_key).length).toBe(2);
	});
});
