import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { page as screen } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../routes/layout.css';
import { resetApp } from '#lib/testing/app.svelte.ts';
import ConfirmField from './ConfirmField.svelte';
import DeleteDatasetsDialog from './datasets/DeleteDatasetsDialog.svelte';
import DeleteBucketDialog from './storage/DeleteBucketDialog.svelte';

vi.mock('$app/forms', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/navigation', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/state', () => import('#lib/testing/app.svelte.ts'));

let clipboard = '';
beforeEach(() => {
	resetApp('http://flock.test/storage/acme');
	clipboard = '';
	vi.spyOn(navigator.clipboard, 'writeText').mockImplementation(async (text) => {
		clipboard = text;
	});
});
afterEach(() => vi.restoreAllMocks());

describe('ConfirmField', () => {
	it('copies the exact text, shows it was copied and puts focus in the input', async () => {
		render(ConfirmField, { id: 'confirm', text: 'eval/deep/mmlu', value: '' });
		const input = screen.getByLabelText('Type eval/deep/mmlu to confirm');
		await expect.element(input).toBeVisible();
		await screen.getByRole('button', { name: 'Copy' }).click();
		expect(clipboard).toBe('eval/deep/mmlu');
		await expect.element(screen.getByRole('button', { name: 'Copied' })).toBeVisible();
		await expect.element(input).toHaveFocus();
	});

	it('lets the text be selected', async () => {
		render(ConfirmField, { id: 'confirm', text: 'acme/olmo2-1b@11', value: '' });
		const text = screen.getByText('acme/olmo2-1b@11').element();
		expect(getComputedStyle(text).userSelect).not.toBe('none');
		expect(getComputedStyle(text.parentElement!).userSelect).not.toBe('none');
		const range = document.createRange();
		range.selectNodeContents(text);
		const selection = window.getSelection()!;
		selection.removeAllRanges();
		selection.addRange(range);
		expect(selection.toString()).toBe('acme/olmo2-1b@11');
	});

	it('says the words around the text that are asked for', async () => {
		render(ConfirmField, {
			id: 'confirm',
			text: 'acme',
			value: '',
			before: 'Type its ID,',
			after: ', to confirm'
		});
		await expect.element(screen.getByLabelText('Type its ID, acme, to confirm')).toBeVisible();
	});
});

describe('a delete dialog with ConfirmField', () => {
	it('enables Delete once the pasted text matches', async () => {
		render(DeleteBucketDialog, { open: true, bucket: 'scratch' });
		const confirm = screen.getByRole('button', { name: 'Delete bucket' });
		await expect.element(confirm).toBeDisabled();
		await screen.getByRole('button', { name: 'Copy' }).click();
		const input = screen.getByLabelText('Type scratch to confirm');
		await expect.element(input).toHaveFocus();
		await input.fill(clipboard);
		await expect.element(confirm).toBeEnabled();
		await input.fill('scratc');
		await expect.element(confirm).toBeDisabled();
	});

	it('asks for the number of items when there are several', async () => {
		render(DeleteDatasetsDialog, {
			open: true,
			org: 'acme',
			items: [
				{ path: 'eval/a', folder: false },
				{ path: 'eval/b', folder: true }
			],
			ondeleted: () => {}
		});
		const confirm = screen.getByRole('button', { name: 'Delete 2 items' });
		await screen.getByRole('button', { name: 'Copy' }).click();
		expect(clipboard).toBe('2');
		await screen.getByLabelText('Type 2 to confirm').fill(clipboard);
		await expect.element(confirm).toBeEnabled();
	});
});
