import { describe, expect, it } from 'vitest';
import { page } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../routes/layout.css';
import type { CreatedHubKey } from '#lib/types.ts';
import HubKeyCreator from './HubKeyCreator.svelte';

const orgs = [
	{ name: 'flock', display_name: 'Flock' },
	{ name: 'lab', display_name: 'The Lab' }
];

const created: CreatedHubKey = {
	pk: 7,
	name: 'cluster',
	access_key_id: 'FLKHABCDEFGHIJKLMNOP',
	read_only: true,
	org: 'flock',
	created_at: 0,
	last_used_at: null,
	secret_access_key: 'c2VjcmV0LXNlY3JldA',
	endpoint: 'https://hub.flock.test',
	region: 'us-east-1'
};

describe('HubKeyCreator', () => {
	it('asks for a name, an organization and whether it only reads', async () => {
		render(HubKeyCreator, { orgs });
		await page.getByRole('button', { name: 'Create S3 key' }).click();
		const dialog = page.getByRole('dialog', { name: 'New S3 key' });
		await expect.element(dialog).toBeVisible();
		await expect.element(dialog.getByLabelText('Name')).toBeVisible();
		await expect.element(dialog.getByRole('checkbox', { name: /Read only/ })).not.toBeChecked();
		await expect.element(dialog.getByText('Every organization')).toBeVisible();
		const form = document.querySelector('form')!;
		expect(form.getAttribute('action')).toBe('/account?/createHubKey');
		expect(new FormData(form).get('org')).toBe('/');
	});

	it('shows a new key’s secret once, with the AWS CLI setup', async () => {
		render(HubKeyCreator, { orgs, open: true, created });
		const dialog = page.getByRole('dialog', { name: 'S3 key “cluster”' });
		await expect.element(dialog).toBeVisible();
		await expect.element(dialog.getByText(created.access_key_id, { exact: true })).toBeVisible();
		await expect
			.element(dialog.getByText(created.secret_access_key, { exact: true }))
			.toBeVisible();
		await expect.element(dialog.getByText(/isn’t shown again/)).toBeVisible();
		const snippet = [...document.querySelectorAll('pre code')].map((c) => c.textContent).join('\n');
		expect(snippet).toContain(`aws configure set aws_access_key_id ${created.access_key_id}`);
		expect(snippet).toContain(
			'aws configure set endpoint_url https://hub.flock.test --profile flock'
		);

		// Closing it forgets the secret: opening again starts a new key.
		await dialog.getByRole('button', { name: 'Done' }).click();
		await expect.element(dialog).not.toBeInTheDocument();
		await page.getByRole('button', { name: 'Create S3 key' }).click();
		await expect.element(page.getByRole('dialog', { name: 'New S3 key' })).toBeVisible();
		expect(document.body.textContent).not.toContain(created.secret_access_key);
	});
});
