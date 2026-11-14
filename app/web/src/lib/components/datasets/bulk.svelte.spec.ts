import { createRawSnippet } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { page as screen, userEvent } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../../routes/layout.css';
import { Selection } from '#lib/selection.svelte.ts';
import { goto, invalidateAll, resetApp } from '#lib/testing/app.svelte.ts';
import type { DatasetFolder, DeleteResult, MoveResult, TrashEntry } from '#lib/types.ts';
import SelectionBar from '../SelectionBar.svelte';
import DeletedDatasets from './DeletedDatasets.svelte';
import FolderView from './FolderView.svelte';

vi.mock('$app/forms', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/navigation', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/state', () => import('#lib/testing/app.svelte.ts'));

/** Answers the dashboard's dataset endpoints by URL; each call is recorded. */
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

beforeEach(() => resetApp('http://flock.test/datasets/acme/eval'));
afterEach(() => {
	vi.unstubAllGlobals();
	invalidateAll.mockClear();
});

const dataset = (name: string) => ({
	name,
	path: `eval/${name}`,
	version: 1,
	updated_at: null,
	size: null
});

const folder = (names = ['a', 'b', 'c'], cursor: string | null = null): DatasetFolder => ({
	kind: 'folder',
	path: 'eval',
	datasets: names.map(dataset),
	folders: [{ name: 'deep', path: 'eval/deep' }],
	files: [{ name: 'notes.md', key: 'eval/notes.md', size: 10, last_modified: 1_790_000_000 }],
	cursor
});

const box = (name: string) => screen.getByRole('checkbox', { name: `Select ${name}`, exact: true });
const opacity = (name: string) => getComputedStyle(box(name).element()).opacity;
const names = () =>
	[...document.querySelectorAll('tbody tr td:nth-child(2)')].map((td) => td.textContent!.trim());

function list(f = folder()) {
	const selection = new Selection();
	render(FolderView, { org: 'acme', path: 'eval', folder: f, now: 1_790_000_100, selection });
	return selection;
}

describe('selecting datasets and folders', () => {
	it('shows a row’s checkbox on hover, then every one once something is checked', async () => {
		const selection = list();
		expect(opacity('a')).toBe('0');
		await screen.getByRole('link', { name: 'a', exact: true }).hover();
		await vi.waitFor(() => expect(opacity('a')).toBe('1'));
		expect(opacity('b')).toBe('0');
		// Loose files can't be checked.
		await expect
			.element(screen.getByRole('checkbox', { name: 'Select notes.md' }))
			.not.toBeInTheDocument();
		await box('a').click();
		expect([...selection.selected]).toEqual(['eval/a']);
		await vi.waitFor(() => expect(opacity('deep/')).toBe('1'));
		const row = box('a').element().closest('tr')!;
		expect(row.getAttribute('aria-selected')).toBe('true');
		expect(row.className).toContain('bg-selection/45');
	});

	it('checks a range with shift-click, and all or none from the header', async () => {
		const selection = list();
		await box('a').click();
		await box('deep/').click({ modifiers: ['Shift'] });
		expect(selection.chosen).toEqual(['eval/a', 'eval/b', 'eval/c', 'eval/deep']);
		await box('b').click();
		await box('c').click({ modifiers: ['Shift'] });
		expect(selection.chosen).toEqual(['eval/a', 'eval/deep']);
		const all = screen.getByRole('checkbox', { name: 'Select all' });
		await expect.element(all).toHaveAttribute('aria-checked', 'mixed');
		await all.click();
		expect(selection.chosen).toEqual(['eval/a', 'eval/b', 'eval/c', 'eval/deep']);
		await screen.getByRole('checkbox', { name: 'Deselect all' }).click();
		expect(selection.size).toBe(0);
	});

	it('keeps the selection while more rows load', async () => {
		serve({
			'/api/datasets/acme/at': () => ({ ...folder(['d']), folders: [], files: [], cursor: null })
		});
		const selection = list(folder(['a', 'b', 'c'], 'next'));
		await box('b').click();
		await screen.getByRole('button', { name: 'Load more' }).click();
		await expect.element(box('d')).toBeInTheDocument();
		expect(selection.chosen).toEqual(['eval/b']);
		expect(selection.keys).toContain('eval/d');
	});

	it('shows no checkboxes or menus without a selection to fill', async () => {
		render(FolderView, { org: 'acme', path: 'eval', folder: folder(), now: 0 });
		await expect.element(screen.getByRole('link', { name: 'a', exact: true })).toBeVisible();
		expect(document.querySelectorAll('[role="checkbox"]').length).toBe(0);
		await expect
			.element(screen.getByRole('button', { name: 'Actions for a' }))
			.not.toBeInTheDocument();
	});
});

describe('SelectionBar', () => {
	const actions = createRawSnippet(() => ({ render: () => '<button>Move</button>' }));

	it('counts the rows checked, and clears with Clear or Escape', async () => {
		const selection = new Selection();
		selection.keys = ['a', 'b', 'c'];
		selection.toggleAll();
		render(SelectionBar, { selection, children: actions });
		await expect.element(screen.getByRole('toolbar')).toHaveTextContent('3 selected');
		await expect.element(screen.getByRole('button', { name: 'Move' })).toBeVisible();
		await screen.getByRole('button', { name: 'Clear' }).click();
		expect(selection.size).toBe(0);
		selection.click('b');
		await userEvent.keyboard('{Escape}');
		expect(selection.size).toBe(0);
	});
});

describe('the row menu', () => {
	it('is on datasets and folders, not files, and renames a folder by its full path', async () => {
		const calls = serve({ '/api/datasets/acme/rename': () => ({ path: 'archive/deep' }) });
		list();
		await expect
			.element(screen.getByRole('button', { name: 'Actions for notes.md' }))
			.not.toBeInTheDocument();
		await screen.getByRole('button', { name: 'Actions for deep/' }).click();
		await expect.element(screen.getByRole('menuitem', { name: 'Delete…' })).toBeVisible();
		await screen.getByRole('menuitem', { name: 'Rename…' }).click();
		await expect.element(screen.getByRole('heading', { name: 'Rename eval/deep/' })).toBeVisible();
		await expect.element(screen.getByText('Everything in it moves with it.')).toBeVisible();
		const input = screen.getByLabelText('New path');
		await expect.element(input).toHaveValue('eval/deep');
		await input.fill('archive/deep');
		await screen.getByRole('button', { name: 'Rename' }).click();
		// It left this folder: its row goes, with nothing read again and no page opened.
		await vi.waitFor(() => expect(names()).toEqual(['a', 'b', 'c', 'notes.md']));
		expect(body(calls[0].init)).toEqual({ path: 'eval/deep', to: 'archive/deep' });
		expect(invalidateAll).not.toHaveBeenCalled();
		expect(goto).not.toHaveBeenCalled();
	});

	it('deletes one dataset after its path is typed', async () => {
		const calls = serve({
			'/api/datasets/acme/delete': () => ({
				results: [{ path: 'eval/b', deleted: true, error: null, entry: null }]
			})
		});
		list();
		await screen.getByRole('button', { name: 'Actions for b' }).click();
		await screen.getByRole('menuitem', { name: 'Delete…' }).click();
		await expect.element(screen.getByRole('heading', { name: 'Delete b?' })).toBeVisible();
		await expect.element(screen.getByText('Org admins can restore it for 14 days.')).toBeVisible();
		const confirm = screen.getByRole('button', { name: 'Delete dataset' });
		await expect.element(confirm).toBeDisabled();
		await screen.getByLabelText('Type eval/b to confirm').fill('eval/b');
		await confirm.click();
		await vi.waitFor(() => expect(names()).toEqual(['a', 'c', 'deep/', 'notes.md']));
		expect(body(calls[0].init)).toEqual({ paths: ['eval/b'] });
	});
});

describe('MoveDatasetsDialog', () => {
	it('moves the rows checked into a folder picked, showing those that didn’t move', async () => {
		const results: MoveResult[] = [
			{ path: 'eval/a', to: 'eval/deep/a', moved: true, error: null, taken: false },
			{
				path: 'eval/deep',
				to: 'eval/deep/deep',
				moved: false,
				error: 'A folder can’t move into itself.',
				taken: false
			}
		];
		const calls = serve({
			'/api/datasets/acme/at?path=eval&': () => ({
				...folder(),
				folders: [
					{ name: 'deep', path: 'eval/deep' },
					{ name: 'old', path: 'eval/old' }
				]
			}),
			'/api/datasets/acme/at?path=eval%2Fold': () => ({ ...folder([]), folders: [] }),
			'/api/datasets/acme/move': () => ({ results })
		});
		const selection = list();
		await box('a').click();
		await box('deep/').click();
		selection.act('move');
		await expect.element(screen.getByRole('heading', { name: 'Move 2 items' })).toBeVisible();
		const to = screen.getByLabelText('Folder', { exact: true });
		await expect.element(to).toHaveValue('eval');
		// The picker browses folders only, without the ones being moved.
		const picker = screen.getByRole('group', { name: 'Folders' });
		await expect.element(picker.getByRole('button', { name: 'old/' })).toBeVisible();
		await expect.element(picker.getByRole('button', { name: 'deep/' })).not.toBeInTheDocument();
		expect(calls[0].url).toBe('/api/datasets/acme/at?path=eval&details=0');
		await picker.getByRole('button', { name: 'old/' }).click();
		await expect.element(to).toHaveValue('eval/old');
		await expect.element(picker.getByText('No folders here.')).toBeVisible();
		await picker.getByRole('button', { name: 'eval' }).click();
		await expect.element(to).toHaveValue('eval');
		await to.fill('eval/deep');
		await screen.getByRole('button', { name: 'Move', exact: true }).click();
		await expect.element(screen.getByText('1 item didn’t move.')).toBeVisible();
		await expect.element(screen.getByText('A folder can’t move into itself.')).toBeVisible();
		expect(body(calls.find((c) => c.url.endsWith('/move'))!.init)).toEqual({
			paths: ['eval/a', 'eval/deep'],
			to: 'eval/deep'
		});
		// The row moved leaves the list and the selection; this folder is read again.
		expect(names()).toEqual(['b', 'c', 'deep/', 'notes.md']);
		expect(selection.chosen).toEqual(['eval/deep']);
		expect(invalidateAll).toHaveBeenCalled();
	});

	it('refuses a destination the items are already in, and shows the command over the limits', async () => {
		serve({
			'/api/datasets/acme/at': () => ({ ...folder(), folders: [] }),
			'/api/datasets/acme/move': () =>
				Response.json(
					{ message: 'That’s more than 10,000 objects.', command: 'aws s3 mv …' },
					{ status: 409 }
				)
		});
		const selection = list();
		await box('a').click();
		selection.act('move');
		await screen.getByRole('button', { name: 'Move', exact: true }).click();
		await expect
			.element(screen.getByRole('alert'))
			.toHaveTextContent('It’s already in this folder');
		await screen.getByLabelText('Folder', { exact: true }).fill('archive');
		await screen.getByRole('button', { name: 'Move', exact: true }).click();
		await expect
			.element(screen.getByRole('alert'))
			.toHaveTextContent('That’s more than 10,000 objects.');
		await expect.element(screen.getByText('aws s3 mv …')).toBeVisible();
		expect(names()).toEqual(['a', 'b', 'c', 'deep/', 'notes.md']);
	});
});

describe('DeleteDatasetsDialog', () => {
	it('lists up to 10 names, asks for the count, and drops the rows deleted', async () => {
		const many = 'abcdefghijkl'.split('');
		const calls = serve({
			'/api/datasets/acme/delete': (init) => ({
				results: (body(init).paths as string[]).map((path): DeleteResult =>
					path === 'eval/l'
						? { path, deleted: false, error: 'eval/l is being written to.', entry: null }
						: { path, deleted: true, error: null, entry: null }
				)
			})
		});
		const selection = list(folder(many));
		await screen.getByRole('checkbox', { name: 'Select all' }).click();
		selection.act('delete');
		await expect.element(screen.getByRole('heading', { name: 'Delete 13 items?' })).toBeVisible();
		await expect
			.element(
				screen.getByText(
					'Folders are deleted with everything in them. Org admins can restore them for 14 days.'
				)
			)
			.toBeVisible();
		const dialog = screen.getByRole('alertdialog');
		await expect.element(dialog.getByText('eval/j', { exact: true })).toBeVisible();
		await expect.element(dialog.getByText('eval/k', { exact: true })).not.toBeInTheDocument();
		await expect.element(dialog.getByText('and 3 more')).toBeVisible();
		const confirm = screen.getByRole('button', { name: 'Delete 13 items' });
		await expect.element(confirm).toBeDisabled();
		await screen.getByLabelText('Type 13 to confirm').fill('13');
		await confirm.click();
		await expect.element(screen.getByText('1 item wasn’t deleted.')).toBeVisible();
		await expect.element(screen.getByText('eval/l is being written to.')).toBeVisible();
		expect(body(calls[0].init).paths).toHaveLength(13);
		expect(names()).toEqual(['l', 'notes.md']);
		expect(selection.chosen).toEqual(['eval/l']);
	});
});

describe('DeletedDatasets with folders', () => {
	it('shows a deleted folder with a folder icon, and restores it', async () => {
		const now = 1_790_000_000;
		const entry: TrashEntry = {
			id: 4,
			kind: 'folder',
			path: 'eval/old',
			deleted_by: null,
			deleted_at: now - 86_400,
			moved_at: now,
			expires_at: now + 13 * 86_400,
			latest_version: null,
			objects: 40,
			bytes: 1024
		};
		const calls = serve({ '/api/datasets/acme/trash/4/restore': () => ({ path: 'eval/old' }) });
		render(DeletedDatasets, { org: 'acme', entries: [entry], now });
		await expect.element(screen.getByText('eval/old/')).toBeVisible();
		await expect.element(screen.getByLabelText('Folder')).toBeInTheDocument();
		await screen.getByRole('button', { name: 'Restore' }).click();
		await expect
			.element(screen.getByText('It goes back to eval/old with everything in it.'))
			.toBeVisible();
		await screen.getByRole('dialog').getByRole('button', { name: 'Restore' }).click();
		await vi.waitFor(() => expect(calls).toHaveLength(1));
		expect(calls[0].url).toBe('/api/datasets/acme/trash/4/restore');
	});
});
