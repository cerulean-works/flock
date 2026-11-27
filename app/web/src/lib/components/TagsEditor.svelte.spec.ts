import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { page as screen, userEvent } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../routes/layout.css';
import { invalidateAll, resetApp } from '#lib/testing/app.svelte.ts';
import TagsEditor from './TagsEditor.svelte';

vi.mock('$app/forms', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/navigation', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/state', () => import('#lib/testing/app.svelte.ts'));

/** The runs' tags by id, and the project's tags with their run counts. */
const fake = vi.hoisted(() => ({
	runs: {} as Record<string, string[]>,
	known: [] as { value: string; runs: number }[]
}));

vi.mock('#lib/workspace/workspace.svelte.ts', () => ({
	getWorkspace: () => ({
		meta: { entity: 'acme', project: 'bert' },
		group: null,
		rowsOf: (ids: readonly string[]) => [...ids],
		cell: (id: string, key: string) => (key === 'tags' ? fake.runs[id] : undefined)
	})
}));
vi.mock('#lib/data/run-values.svelte.ts', () => ({
	runValues: () => ({ tags: fake.known }),
	forgetRunValues: () => {}
}));
vi.mock('#lib/data/cache.ts', () => ({ clearRunPages: () => {} }));

/** Each `tag` action posted: the runs, and what it adds or removes. */
let posts: { runs: string[]; add: string; remove: string | null }[] = [];

beforeEach(() => {
	resetApp('http://flock.test/acme/bert');
	posts = [];
	vi.stubGlobal(
		'fetch',
		vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
			const body = init?.body as FormData;
			posts.push({
				runs: body.getAll('run').map(String),
				add: String(body.get('add')),
				remove: body.get('remove') as string | null
			});
			return new Response(JSON.stringify({ type: 'success', status: 200 }));
		})
	);
});
afterEach(() => {
	vi.unstubAllGlobals();
	invalidateAll.mockClear();
});

const field = () => screen.getByRole('combobox', { name: 'Add a tag' });

describe('TagsEditor', () => {
	it('adds one of the project’s tags from the list', async () => {
		fake.runs = { a: [], b: [] };
		fake.known = [
			{ value: 'baseline', runs: 4 },
			{ value: 'sweep', runs: 2 }
		];
		render(TagsEditor, { ids: ['a', 'b'] });
		await screen.getByRole('option', { name: /sweep/ }).getByRole('button').click();
		await expect.poll(() => posts).toEqual([{ runs: ['a', 'b'], add: 'sweep', remove: null }]);
		// A chip, and gone from the options now that every run has it.
		await expect.element(screen.getByRole('button', { name: 'Remove sweep' })).toBeVisible();
		await expect.element(screen.getByRole('option', { name: /sweep/ })).not.toBeInTheDocument();
		expect(invalidateAll).toHaveBeenCalled();
	});

	it('filters as you type, and Enter takes the highlighted tag', async () => {
		fake.runs = { a: [] };
		fake.known = [
			{ value: 'baseline', runs: 4 },
			{ value: 'sweep', runs: 2 }
		];
		render(TagsEditor, { ids: ['a'] });
		await field().fill('swe');
		await expect.element(screen.getByRole('option', { name: /baseline/ })).not.toBeInTheDocument();
		await userEvent.keyboard('{Enter}');
		await expect.poll(() => posts.map((p) => p.add)).toEqual(['sweep']);
		await expect.element(field()).toHaveValue('');
	});

	it('creates a new tag from what was typed', async () => {
		fake.runs = { a: [] };
		fake.known = [{ value: 'baseline', runs: 4 }];
		render(TagsEditor, { ids: ['a'] });
		await field().fill('ablation');
		await expect.element(screen.getByRole('option', { name: 'Create “ablation”' })).toBeVisible();
		await userEvent.keyboard('{Enter}');
		await expect.poll(() => posts.map((p) => p.add)).toEqual(['ablation']);
		await expect.element(screen.getByRole('button', { name: 'Remove ablation' })).toBeVisible();
	});

	it('a comma ends a tag too; spaces never make one', async () => {
		fake.runs = { a: [] };
		fake.known = [];
		render(TagsEditor, { ids: ['a'] });
		await field().fill('two words');
		expect(screen.getByRole('option', { name: /Create/ }).query()).toBeNull();
		await field().fill('lr');
		await userEvent.keyboard(',');
		await expect.poll(() => posts.map((p) => p.add)).toEqual(['lr']);
	});

	it('Backspace in an empty field removes the last chip', async () => {
		fake.runs = { a: ['alpha', 'beta'] };
		fake.known = [
			{ value: 'alpha', runs: 1 },
			{ value: 'beta', runs: 1 }
		];
		render(TagsEditor, { ids: ['a'] });
		await expect.element(screen.getByRole('button', { name: 'Remove beta' })).toBeVisible();
		(field().element() as HTMLElement).focus();
		await userEvent.keyboard('{Backspace}');
		await expect.poll(() => posts).toEqual([{ runs: ['a'], add: '', remove: 'beta' }]);
		await expect
			.element(screen.getByRole('button', { name: 'Remove beta' }))
			.not.toBeInTheDocument();
		await expect.element(screen.getByRole('button', { name: 'Remove alpha' })).toBeVisible();
	});

	it('Backspace while typing edits the text, not the chips', async () => {
		fake.runs = { a: ['alpha'] };
		fake.known = [{ value: 'alpha', runs: 1 }];
		render(TagsEditor, { ids: ['a'] });
		await field().fill('x');
		await userEvent.keyboard('{Backspace}');
		await expect.element(field()).toHaveValue('');
		expect(posts).toEqual([]);
		await expect.element(screen.getByRole('button', { name: 'Remove alpha' })).toBeVisible();
	});

	it('a tag on only some runs is a partial chip; a click puts it on all of them', async () => {
		fake.runs = { a: ['shared', 'half'], b: ['shared'] };
		fake.known = [
			{ value: 'shared', runs: 2 },
			{ value: 'half', runs: 1 }
		];
		render(TagsEditor, { ids: ['a', 'b'] });
		const partial = screen.getByRole('button', { name: 'Add half to every run' });
		await expect.element(partial).toHaveTextContent('half 1 of 2');
		// A tag every run has is a plain chip, not a button.
		expect(screen.getByRole('button', { name: 'Add shared to every run' }).query()).toBeNull();
		// Still offered below, since not every run has it.
		await expect.element(screen.getByRole('option', { name: /half/ })).toBeVisible();
		await partial.click();
		await expect.poll(() => posts).toEqual([{ runs: ['a', 'b'], add: 'half', remove: null }]);
		await expect
			.element(screen.getByRole('button', { name: 'Add half to every run' }))
			.not.toBeInTheDocument();
		await expect.element(screen.getByRole('button', { name: 'Remove half' })).toBeVisible();
	});

	it('removing a chip with its X removes it from every run', async () => {
		fake.runs = { a: ['half'], b: [] };
		fake.known = [{ value: 'half', runs: 1 }];
		render(TagsEditor, { ids: ['a', 'b'] });
		await screen.getByRole('button', { name: 'Remove half' }).click();
		await expect.poll(() => posts).toEqual([{ runs: ['a', 'b'], add: '', remove: 'half' }]);
	});
});
