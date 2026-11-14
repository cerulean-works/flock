import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { page as screen } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../../routes/layout.css';
import { Selection } from '#lib/selection.svelte.ts';
import { goto, invalidateAll, resetApp } from '#lib/testing/app.svelte.ts';
import type { ModelEntry } from '#lib/types.ts';
import ModelList from './ModelList.svelte';
import RenameModelDialog from './RenameModelDialog.svelte';

vi.mock('$app/forms', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/navigation', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/state', () => import('#lib/testing/app.svelte.ts'));

/** Answers the dashboard's endpoints by URL (the longest prefix wins); each call is recorded. */
function serve(routes: Record<string, (init?: RequestInit, url?: string) => unknown>) {
	const calls: { url: string; init?: RequestInit }[] = [];
	vi.stubGlobal(
		'fetch',
		vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
			const url = String(input);
			calls.push({ url, init });
			const route = Object.keys(routes)
				.filter((r) => url.startsWith(r))
				.sort((a, b) => b.length - a.length)[0];
			if (!route) return Response.json({ message: 'not found' }, { status: 404 });
			const body = routes[route](init, url);
			return body instanceof Response ? body : Response.json(body);
		})
	);
	return calls;
}

const body = (init?: RequestInit) => JSON.parse(String(init?.body));
const API = '/api/models/acme';
const NOW = 1_790_000_000;

const entry = (name: string): ModelEntry => ({
	name,
	model: `acme/${name}`,
	created_at: NOW - 86_400,
	created_by: null,
	versions: 2,
	latest: 2,
	primary: 2,
	size: null,
	updated_at: NOW - 3600
});

beforeEach(() => resetApp('http://flock.test/models/acme'));
afterEach(() => {
	vi.unstubAllGlobals();
	invalidateAll.mockClear();
});

const box = (name: string) => screen.getByRole('checkbox', { name: `Select ${name}`, exact: true });
const names = () =>
	[...document.querySelectorAll('tbody tr td:nth-child(2)')].map((td) => td.textContent!.trim());

function list(models = ['a', 'b', 'c'].map(entry)) {
	const selection = new Selection();
	render(ModelList, { org: 'acme', models, now: NOW, selection });
	return selection;
}

describe('selecting models', () => {
	it('checks models from checkboxes shown on hover, with shift-click and the header', async () => {
		const selection = list();
		expect(getComputedStyle(box('a').element()).opacity).toBe('0');
		await screen.getByRole('link', { name: 'a', exact: true }).hover();
		await vi.waitFor(() => expect(getComputedStyle(box('a').element()).opacity).toBe('1'));
		await box('a').click();
		await box('c').click({ modifiers: ['Shift'] });
		expect(selection.chosen).toEqual(['a', 'b', 'c']);
		await expect
			.element(screen.getByRole('checkbox', { name: 'Deselect all' }))
			.toHaveAttribute('aria-checked', 'true');
		await screen.getByRole('checkbox', { name: 'Deselect all' }).click();
		expect(selection.size).toBe(0);
	});

	it('deletes several after their number is typed, showing those that weren’t', async () => {
		const calls = serve({
			[`${API}/a`]: () => ({ kind: 'model', id: 1 }),
			[`${API}/b`]: () => Response.json({ message: 'An upload to b is open.' }, { status: 409 }),
			[`${API}/c`]: () => ({ kind: 'model', id: 3 })
		});
		const selection = list();
		await screen.getByRole('checkbox', { name: 'Select all' }).click();
		selection.act('delete');
		await expect.element(screen.getByRole('heading', { name: 'Delete 3 models?' })).toBeVisible();
		await expect.element(screen.getByText('acme/b')).toBeVisible();
		const confirm = screen.getByRole('button', { name: 'Delete 3 models' });
		await expect.element(confirm).toBeDisabled();
		await screen.getByLabelText('Type 3 to confirm').fill('3');
		await confirm.click();
		await expect.element(screen.getByText('1 model wasn’t deleted.')).toBeVisible();
		await expect.element(screen.getByText('An upload to b is open.')).toBeVisible();
		expect(calls.map((c) => [c.url, c.init?.method])).toEqual([
			[`${API}/a`, 'DELETE'],
			[`${API}/b`, 'DELETE'],
			[`${API}/c`, 'DELETE']
		]);
		expect(names()).toEqual(['b']);
		expect(selection.chosen).toEqual(['b']);
		expect(goto).not.toHaveBeenCalled();
	});

	it('deletes one checked model with its own dialog, by its name', async () => {
		const calls = serve({ [`${API}/b`]: () => ({ kind: 'model', id: 2 }) });
		const selection = list();
		await box('b').click();
		selection.act('delete');
		await expect.element(screen.getByRole('heading', { name: 'Delete b?' })).toBeVisible();
		await screen.getByLabelText('Type acme/b to confirm').fill('acme/b');
		await screen.getByRole('button', { name: 'Delete model' }).click();
		await vi.waitFor(() => expect(names()).toEqual(['a', 'c']));
		expect(calls).toHaveLength(1);
		expect(selection.size).toBe(0);
		expect(goto).not.toHaveBeenCalled();
	});
});

describe('the model row menu', () => {
	it('renames a model in place', async () => {
		const calls = serve({ [`${API}/b`]: () => ({ ...entry('bert'), prefix: 'b/' }) });
		list();
		await screen.getByRole('button', { name: 'Actions for b' }).click();
		await screen.getByRole('menuitem', { name: 'Rename…' }).click();
		const input = screen.getByLabelText('New name');
		await expect.element(input).toHaveValue('b');
		await input.fill('Bert');
		await screen.getByRole('button', { name: 'Rename' }).click();
		await expect.element(screen.getByRole('alert')).toBeVisible();
		expect(calls).toEqual([]);
		await input.fill('bert');
		await screen.getByRole('button', { name: 'Rename' }).click();
		await vi.waitFor(() => expect(names()).toEqual(['a', 'bert', 'c']));
		expect([calls[0].init?.method, body(calls[0].init)]).toEqual(['PATCH', { name: 'bert' }]);
		expect(screen.getByRole('link', { name: 'bert' }).element().getAttribute('href')).toBe(
			'/models/acme/bert'
		);
		expect(goto).not.toHaveBeenCalled();
	});

	it('opens the delete dialog for its model', async () => {
		list();
		await screen.getByRole('button', { name: 'Actions for c' }).click();
		await screen.getByRole('menuitem', { name: 'Delete…' }).click();
		await expect.element(screen.getByLabelText('Type acme/c to confirm')).toBeVisible();
	});
});

describe('RenameModelDialog', () => {
	it('shows a refusal, then opens the model’s new page', async () => {
		let taken = true;
		const calls = serve({
			[`${API}/olmo2-1b`]: () =>
				taken
					? Response.json({ message: 'A model named olmo2 already exists.' }, { status: 409 })
					: { ...entry('olmo2'), prefix: 'olmo2-1b/' }
		});
		render(RenameModelDialog, { open: true, org: 'acme', name: 'olmo2-1b' });
		await expect
			.element(
				screen.getByText(
					'Code that loads it by its old name stops working. Its files stay where they are.'
				)
			)
			.toBeVisible();
		await screen.getByLabelText('New name').fill('olmo2');
		await screen.getByRole('button', { name: 'Rename' }).click();
		await expect
			.element(screen.getByRole('alert'))
			.toHaveTextContent('A model named olmo2 already exists.');
		taken = false;
		await screen.getByRole('button', { name: 'Rename' }).click();
		await vi.waitFor(() =>
			expect(goto).toHaveBeenCalledWith('/models/acme/olmo2', { replace: true })
		);
		expect(calls).toHaveLength(2);
	});
});
