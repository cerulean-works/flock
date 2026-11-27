import { beforeEach, describe, expect, it, vi } from 'vitest';
import { page, userEvent } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../routes/layout.css';
import { app, resetApp } from '#lib/testing/app.svelte.ts';
import type { UserInfo } from '#lib/types.ts';
import ProfileSettings from './ProfileSettings.svelte';

vi.mock('$app/forms', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/navigation', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/state', () => import('#lib/testing/app.svelte.ts'));

const ADA: UserInfo = {
	id: 1,
	email: 'ada@example.com',
	name: 'Ada Lovelace',
	is_superadmin: false,
	has_password: true,
	google: false,
	default_org: null,
	orgs: [],
	created_at: 1_790_000_000,
	avatar_url: null,
	avatar_source: null
};
// A 1×1 PNG, so the picture loads rather than falling back to initials.
const PICTURE =
	'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=';

beforeEach(() => resetApp('http://flock.test/account'));

const dialog = () => page.getByRole('dialog');

describe('the profile section', () => {
	it('shows the picture, the name, the email and the password button', async () => {
		render(ProfileSettings, { profile: { ...ADA, avatar_url: PICTURE } });
		await expect.element(page.getByRole('heading', { name: 'Profile' })).toBeVisible();
		await expect.element(page.getByLabelText('Name')).toHaveValue('Ada Lovelace');
		await expect.element(page.getByLabelText('Email')).toHaveValue('ada@example.com');
		await expect.element(page.getByLabelText('Email')).toHaveAttribute('readonly');
		await expect.element(page.getByRole('button', { name: 'Change password' })).toBeVisible();
		expect(document.querySelector('section img')!.getAttribute('src')).toBe(PICTURE);
	});

	it('has the picture controls, and no Gravatar button', async () => {
		render(ProfileSettings, { profile: { ...ADA, avatar_url: PICTURE, avatar_source: 'upload' } });
		await expect.element(page.getByRole('button', { name: 'Change picture' })).toBeVisible();
		await expect.element(page.getByRole('button', { name: 'Remove' })).toBeVisible();
		await expect.element(page.getByText('PNG, JPEG, GIF or WebP, up to 1 MB.')).toBeVisible();
		expect(document.body.textContent).not.toMatch(/gravatar/i);
	});

	it('shows initials and offers an upload without a picture, and nothing to remove', async () => {
		render(ProfileSettings, { profile: ADA });
		await expect.element(page.getByText('AL', { exact: true })).toBeVisible();
		await expect.element(page.getByRole('button', { name: 'Upload a picture' })).toBeVisible();
		await expect.element(page.getByRole('button', { name: 'Remove' })).not.toBeInTheDocument();
	});

	it('removes the picture', async () => {
		render(ProfileSettings, { profile: { ...ADA, avatar_url: PICTURE } });
		await page.getByRole('button', { name: 'Remove' }).click();
		await expect.poll(() => app.sent.map((s) => s.action)).toEqual(['/account?/removeAvatar']);
	});

	it('refuses a file of the wrong type without sending it', async () => {
		render(ProfileSettings, { profile: ADA });
		const input = document.querySelector<HTMLInputElement>('input[type=file]')!;
		const files = new DataTransfer();
		files.items.add(new File(['%PDF'], 'cv.pdf', { type: 'application/pdf' }));
		input.files = files.files;
		input.dispatchEvent(new Event('change', { bubbles: true }));
		await expect.element(page.getByText('Use a PNG, JPEG, GIF or WebP image')).toBeVisible();
		expect(app.sent).toEqual([]);
	});

	it('saves the name on Enter, with a tick, and not again when nothing changed', async () => {
		app.result = { type: 'success', status: 200, data: { name: 'Ada King' } };
		render(ProfileSettings, { profile: ADA });
		const field = page.getByLabelText('Name');
		await field.fill('Ada King');
		await userEvent.keyboard('{Enter}');
		await expect.element(page.getByRole('status').getByText('Saved')).toBeVisible();
		expect(app.sent.map((s) => [s.action, s.form.get('name')])).toEqual([
			['/account?/profile', 'Ada King']
		]);
	});

	it('saves the name when the field is left, and sends nothing unchanged', async () => {
		render(ProfileSettings, { profile: ADA });
		const field = page.getByLabelText('Name');
		await field.click();
		await userEvent.keyboard('{Enter}');
		await userEvent.tab();
		expect(app.sent).toEqual([]);
		await field.fill('Ada King');
		await userEvent.tab();
		await expect.poll(() => app.sent.map((s) => s.form.get('name'))).toEqual(['Ada King']);
	});
});

describe('the password dialog', () => {
	it('offers to set a password to an account without one', async () => {
		render(ProfileSettings, { profile: { ...ADA, has_password: false, google: true } });
		await page.getByRole('button', { name: 'Set password' }).click();
		await expect.element(dialog().getByText(/You sign in with Google/)).toBeVisible();
		await expect
			.element(dialog().getByLabelText('Current password', { exact: true }))
			.not.toBeInTheDocument();
	});

	it('has the three fields and says it signs you out elsewhere', async () => {
		render(ProfileSettings, { profile: ADA });
		await page.getByRole('button', { name: 'Change password' }).click();
		await expect
			.element(dialog().getByText('Changing it signs you out everywhere else.'))
			.toBeVisible();
		for (const label of ['Current password', 'New password', 'Confirm new password'])
			await expect
				.element(dialog().getByLabelText(label, { exact: true }))
				.toHaveAttribute('type', 'password');
	});

	it('keeps a refusal in the dialog', async () => {
		app.result = { type: 'failure', status: 400, data: { error: 'The new passwords don’t match' } };
		render(ProfileSettings, { profile: ADA });
		await page.getByRole('button', { name: 'Change password' }).click();
		await dialog().getByLabelText('Current password', { exact: true }).fill('old-password');
		await dialog().getByLabelText('New password', { exact: true }).fill('new-password');
		await dialog().getByLabelText('Confirm new password', { exact: true }).fill('other-password');
		await dialog().getByRole('button', { name: 'Change password' }).click();
		await expect.element(dialog().getByText('The new passwords don’t match')).toBeVisible();
		const [sent] = app.sent;
		expect(sent.action).toBe('/account?/password');
		expect([sent.form.get('current'), sent.form.get('new'), sent.form.get('confirm')]).toEqual([
			'old-password',
			'new-password',
			'other-password'
		]);
	});

	it('closes once the password is changed', async () => {
		render(ProfileSettings, { profile: ADA });
		await page.getByRole('button', { name: 'Change password' }).click();
		await dialog().getByLabelText('Current password', { exact: true }).fill('old-password');
		await dialog().getByLabelText('New password', { exact: true }).fill('new-password');
		await dialog().getByLabelText('Confirm new password', { exact: true }).fill('new-password');
		await dialog().getByRole('button', { name: 'Change password' }).click();
		await expect.element(dialog()).not.toBeInTheDocument();
	});
});
