import { afterEach, describe, expect, it, vi } from 'vitest';
import { page as screen, userEvent } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../../routes/layout.css';
import type { EvalItem, ItemSummary, ItemSuites, ItemsPage, ItemTokens } from '#lib/types.ts';
import EvalItems from './EvalItems.svelte';

const ITEM: EvalItem = {
	task: 'arc_easy',
	metric: 'acc_per_char',
	id: 'Mercury_7017010',
	question: 'Question: Which planet is closest to the Sun?\nAnswer:',
	contexts: null,
	choices: [' Venus', ' Mercury', ' Mars'],
	gold: 1,
	runs: [
		{
			model: 'olmo',
			pick: 1,
			correct: true,
			logprob: [-3, -1, -2],
			prob: [0.09, 0.665, 0.245],
			share: [0.3, 0.4, 0.3]
		},
		{
			model: 'llama',
			pick: 2,
			correct: false,
			logprob: [-2, -3, -1],
			prob: [0.245, 0.09, 0.665],
			share: [0.2, 0.3, 0.5]
		}
	]
};

/** ITEM as a row of the list: Flock's summary, with each run's margin. */
const ROW: ItemSummary = {
	id: 'Mercury_7017010',
	text: 'Question: Which planet is closest to the Sun?',
	gold: 1,
	gold_text: 'Mercury',
	runs: [
		{ correct: true, pick: 1, pick_text: 'Mercury', margin: Math.log(0.4 / 0.3), gold_prob: 0.665 },
		{ correct: false, pick: 2, pick_text: 'Mars', margin: Math.log(0.3 / 0.5), gold_prob: 0.09 }
	]
};

/** A page of the list holding these rows, of `total` in all. */
const rows = (items: ItemSummary[], total = items.length, offset = 0): ItemsPage => ({
	total,
	offset,
	items
});

/** The pair's one suite square, `core`, holding both tasks: piqa's three docs only B got right. */
const SUITES: ItemSuites = {
	suites: [
		{
			suite: 'core',
			tasks: ['piqa', 'arc_easy'],
			counts: { only_a: 0, only_b: 3, both_wrong: 0, both_right: 0 },
			dots: [
				[-0.5, 0.5, 1, 0, 0, 'p-0'],
				[-0.2, 0.4, 1, 0, 1, 'p-1'],
				[-0.1, 0.9, 1, 0, 2, 'p-2']
			]
		}
	]
};

/** ITEM's tokens: A's with text, one token per choice; B has no sidecar. */
const TOKENS: ItemTokens = {
	runs: [
		{
			note: null,
			choices: ITEM.choices.map((c) => ({
				text: [c],
				logprob: [-4],
				rank: [3],
				entropy: [1.5]
			}))
		},
		{ choices: null, note: 'run:acme/evals/r2 has no per-token values for arc_easy' }
	]
};

/**
 * The dashboard's answers: `list` (or `list(offset)`) for a page of rows, TOKENS for a doc's
 * tokens, SUITES for the squares (with
 * `status`, as the list), and ITEM, or a doc of that id, for a doc. The URLs asked for are in its
 * calls.
 */
function answer(status: number, list: unknown | ((offset: number) => unknown)) {
	const fetch = vi.fn(async (input: string) => {
		const url = new URL(input, 'https://flock.test');
		if (url.pathname.endsWith('/item/tokens'))
			return new Response(JSON.stringify(TOKENS), { status: 200 });
		if (url.pathname.endsWith('/items/suites')) {
			const body = status === 200 ? SUITES : list;
			return new Response(JSON.stringify(body), { status });
		}
		if (url.pathname.endsWith('/item')) {
			const id = url.searchParams.get('doc');
			return new Response(JSON.stringify({ ...ITEM, id }), { status: 200 });
		}
		const offset = Number(url.searchParams.get('offset'));
		const body = typeof list === 'function' ? list(offset) : list;
		return new Response(JSON.stringify(body), { status });
	});
	vi.stubGlobal('fetch', fetch);
	return fetch;
}

/** The URLs asked for, in order: `…/items` for a page, `…/item` for a doc. */
const asked = (fetch: ReturnType<typeof answer>) =>
	fetch.mock.calls.map(([input]) => new URL(String(input), 'https://flock.test'));
const lists = (fetch: ReturnType<typeof answer>) =>
	asked(fetch).filter((url) => url.pathname.endsWith('/items'));
const docs = (fetch: ReturnType<typeof answer>) =>
	asked(fetch).filter((url) => url.pathname.endsWith('/item'));

const PAIR = {
	entity: 'acme',
	project: 'evals',
	run: 'r1',
	other: 'acme/evals/r2',
	sides: ['A', 'B'],
	tasks: [
		{ task: 'piqa', counts: { only_a: 0, only_b: 3 } },
		{ task: 'arc_easy', counts: { only_a: 21, only_b: 4 } }
	],
	keeps: [
		{ keep: 'only_a' as const, label: 'Only A right' },
		{ keep: 'only_b' as const, label: 'Only B right' }
	]
};

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('eval items', () => {
	it('shows a row per doc with each run’s outcome, margin and pick, opening to every choice', async () => {
		const fetch = answer(200, rows([ROW], 21));
		const onview = vi.fn();
		render(EvalItems, { ...PAIR, onview });

		// The first task with docs in the first keep, asked of the pair, surest gap first.
		const row = screen.getByRole('row').filter({ hasText: 'Mercury_7017010' });
		await expect.element(row).toBeVisible();
		const [url] = lists(fetch);
		expect(url.pathname).toBe('/api/projects/acme/evals/runs/r1/items');
		expect(Object.fromEntries(url.searchParams)).toEqual({
			task: 'arc_easy',
			keep: 'only_a',
			sort: 'margin',
			offset: '0',
			limit: '20',
			with: 'acme/evals/r2'
		});
		// Margins: A log(0.4 / 0.3), B log(0.3 / 0.5); the gap between them. No doc's text is asked
		// for until it opens.
		expect(
			row
				.getByRole('cell')
				.all()
				.slice(1)
				.map((c) => c.element().textContent!.replace(/\s+/g, ' ').trim())
		).toEqual([
			'Question: Which planet is closest to the Sun?',
			'Mercury',
			'right: Mercury +0.29',
			'wrong: Mars −0.51',
			'+0.80',
			''
		]);
		expect(docs(fetch)).toEqual([]);
		// Two runs' scores are the counts table's, not the list's.
		expect(screen.getByText(/docs only A got right/).elements()).toEqual([]);

		await row.getByRole('button', { name: 'Mercury_7017010' }).click();
		// The doc asked for with the list's task, keep and pair; the whole question in the row, then
		// a row per choice in the same table.
		const [doc] = docs(fetch);
		expect(doc.pathname).toBe('/api/projects/acme/evals/runs/r1/item');
		expect(Object.fromEntries(doc.searchParams)).toEqual({
			doc: 'Mercury_7017010',
			task: 'arc_easy',
			keep: 'only_a',
			with: 'acme/evals/r2'
		});
		expect(onview).toHaveBeenLastCalledWith({
			task: 'arc_easy',
			keep: 'only_a',
			sort: undefined,
			page: undefined,
			item: 'Mercury_7017010'
		});
		const choices = screen.getByRole('table');
		// Each run's pick is named in its cell: A's on the gold choice, B's on Mars.
		const gold = choices.getByRole('row').filter({ hasText: 'gold' });
		await expect.element(gold.getByRole('cell', { name: /^A \(its pick\)/ })).toBeVisible();
		// The prompt's closing "Answer:" cue isn't shown.
		await expect
			.element(row.getByRole('cell').nth(1))
			.toHaveTextContent(/^Question: Which planet is closest to the Sun\?$/);
		const mars = choices.getByRole('row').filter({ hasText: 'Mars' });
		await expect.element(mars.getByRole('cell', { name: /^B \(its pick\)/ })).toBeVisible();
		await expect
			.element(screen.getByRole('cell', { name: /^B \(its pick\)/ }))
			.toHaveAccessibleName('B (its pick): prob 66.5% · logprob -1.00');
	});

	it('tints an opened doc’s tokens by the heat chosen and says why a run has none', async () => {
		const fetch = answer(200, rows([ROW]));
		render(EvalItems, PAIR);
		await screen.getByRole('button', { name: 'Mercury_7017010' }).click();

		const tokens = asked(fetch).find((url) => url.pathname.endsWith('/item/tokens'))!;
		expect(tokens.pathname).toBe('/api/projects/acme/evals/runs/r1/item/tokens');
		expect(Object.fromEntries(tokens.searchParams)).toEqual({
			doc: 'Mercury_7017010',
			task: 'arc_easy',
			keep: 'only_a',
			with: 'acme/evals/r2'
		});
		// A's token under each choice, with its numbers; B's reason behind an icon in its column.
		const venus = screen.elementLocator(
			document.querySelector('[aria-description="log p -4.00 · rank 3 · entropy 1.50"]')!
		);
		await expect.element(venus).toHaveTextContent('Venus');
		await expect.element(screen.getByRole('button', { name: 'Why B has no tokens' })).toBeVisible();
		// 4 nats of 8: half of the full tint.
		expect(venus.element().getAttribute('style')).toContain('var(--primary) 30%');

		// The toolbar's Tint menu sets the heat for every doc.
		const pickTint = async (from: string, to: string) => {
			await screen.getByRole('button', { name: `Tint: ${from}` }).click();
			await screen.getByRole('menuitemradio', { name: to }).click();
			await userEvent.keyboard('{Escape}');
			await expect.element(screen.getByRole('button', { name: `Tint: ${to}` })).toBeVisible();
		};
		await pickTint('Surprisal', 'Entropy');
		// 1.5 nats of 6.
		expect(venus.element().getAttribute('style')).toContain('var(--primary) 15%');
		// B has no tokens, so A vs B tints nothing.
		await pickTint('Entropy', 'A vs B');
		expect(venus.element().getAttribute('style')).toContain(' 0%');
	});

	it('opens where a link left it, ignoring a part that doesn’t fit', async () => {
		const fetch = answer(200, rows([{ ...ROW, id: 'set/7' }], 30, 20));
		const view = { task: 'piqa', keep: 'wrong' as const, sort: 'doc' as const, page: 2 };
		render(EvalItems, { ...PAIR, view: { ...view, item: 'set/7' } });

		// `wrong` isn't a keep of two runs: the first keep instead.
		await expect
			.element(screen.getByRole('button', { name: 'set/7' }))
			.toHaveAttribute('aria-expanded', 'true');
		const [url] = lists(fetch);
		expect(url.searchParams.get('task')).toBe('piqa');
		expect(url.searchParams.get('keep')).toBe('only_a');
		expect(url.searchParams.get('sort')).toBe('doc');
		expect(url.searchParams.get('offset')).toBe('20');
		// The id is a query value, whatever it holds.
		expect(docs(fetch)[0].searchParams.get('doc')).toBe('set/7');
	});

	it('moves through the docs with j and k, opening each while one is open', async () => {
		// Two pages, the first ending on d-1 and the second starting on d-2 (only two rows of each are
		// sent; a total of 40 makes two pages of 20).
		const page = (offset: number) =>
			rows(
				[0, 1].map((i) => ({ ...ROW, id: `d-${offset / 10 + i}` })),
				40,
				offset
			);
		const fetch = answer(200, page);
		const onview = vi.fn();
		render(EvalItems, { ...PAIR, onview });
		const toggle = (id: string) => screen.getByRole('button', { name: id, exact: true });
		await expect.element(toggle('d-0')).toBeVisible();

		await userEvent.keyboard('j');
		await expect.element(toggle('d-0')).toHaveFocus();
		await userEvent.keyboard('{Enter}');
		await expect.element(toggle('d-0')).toHaveAttribute('aria-expanded', 'true');
		await userEvent.keyboard('j');
		await expect.element(toggle('d-1')).toHaveAttribute('aria-expanded', 'true');
		await expect.element(toggle('d-0')).toHaveAttribute('aria-expanded', 'false');
		expect(onview).toHaveBeenLastCalledWith(expect.objectContaining({ item: 'd-1' }));

		// Past the page's last doc: the next page, its first doc open.
		await userEvent.keyboard('j');
		await expect.element(toggle('d-2')).toHaveAttribute('aria-expanded', 'true');
		expect(lists(fetch).at(-1)!.searchParams.get('offset')).toBe('20');
		expect(onview).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2, item: 'd-2' }));
		await userEvent.keyboard('k');
		await expect.element(toggle('d-1')).toHaveAttribute('aria-expanded', 'true');

		// Esc closes it and leaves the focus on its row; o opens it again.
		await userEvent.keyboard('{Escape}');
		await expect.element(toggle('d-1')).toHaveAttribute('aria-expanded', 'false');
		await expect.element(toggle('d-1')).toHaveFocus();
		expect(onview).toHaveBeenLastCalledWith(expect.objectContaining({ item: undefined }));
		await userEvent.keyboard('o');
		await expect.element(toggle('d-1')).toHaveAttribute('aria-expanded', 'true');
	});

	it('switches to the dataset’s order', async () => {
		const fetch = answer(200, rows([ROW]));
		const onview = vi.fn();
		render(EvalItems, { ...PAIR, onview });
		await screen.getByRole('button', { name: 'Order: Margin' }).click();
		await screen.getByRole('menuitemradio', { name: 'Doc order' }).click();
		await vi.waitFor(() => expect(lists(fetch)).toHaveLength(2));
		expect(lists(fetch)[1].searchParams.get('sort')).toBe('doc');
		await expect.element(screen.getByRole('button', { name: 'Order: Doc order' })).toBeVisible();
		expect(onview).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'doc' }));
	});

	it('pages through a keep and starts over on another one', async () => {
		const fetch = answer(200, rows([ROW], 21));
		const onview = vi.fn();
		render(EvalItems, { ...PAIR, onview });

		// Clicking the keep already chosen keeps the pager.
		await screen.getByRole('radio', { name: /Only A right/ }).click();
		await screen.getByRole('button', { name: 'Next page' }).click();
		await vi.waitFor(() => expect(lists(fetch)).toHaveLength(2));
		expect(lists(fetch)[1].searchParams.get('offset')).toBe('20');
		expect(onview).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 }));
		await screen.getByRole('radio', { name: /Only B right/ }).click();
		await vi.waitFor(() => expect(lists(fetch)).toHaveLength(3));
		expect(lists(fetch)[2].searchParams.get('keep')).toBe('only_b');
		expect(lists(fetch)[2].searchParams.get('offset')).toBe('0');
		expect(onview).toHaveBeenLastCalledWith(
			expect.objectContaining({ keep: 'only_b', page: undefined })
		);
		await expect
			.element(screen.getByRole('radio', { name: /Only B right/ }))
			.toHaveTextContent('4');
	});

	it('picks a task from the searchable list with the keys, starting the pager over', async () => {
		const fetch = answer(200, rows([ROW], 21));
		const onview = vi.fn();
		render(EvalItems, { ...PAIR, onview });

		await screen.getByRole('button', { name: 'Next page' }).click();
		await vi.waitFor(() => expect(lists(fetch)).toHaveLength(2));
		await screen.getByRole('button', { name: 'Task' }).click();
		// The search box has the focus; its keys aren't the list's j and k.
		await expect.element(screen.getByRole('combobox', { name: 'Search tasks' })).toHaveFocus();
		await userEvent.keyboard('piq');
		await expect.element(screen.getByRole('option')).toHaveTextContent('piqa');
		await userEvent.keyboard('{Enter}');
		await vi.waitFor(() => expect(lists(fetch)).toHaveLength(3));
		expect(lists(fetch)[2].searchParams.get('task')).toBe('piqa');
		expect(lists(fetch)[2].searchParams.get('offset')).toBe('0');
		expect(onview).toHaveBeenLastCalledWith(
			expect.objectContaining({ task: 'piqa', page: undefined })
		);
		// The field's name says the task shown, since a screen reader reads no value from a button.
		await expect.element(screen.getByRole('button', { name: 'Task: piqa' })).toBeVisible();
	});

	it('draws a square per suite whose quadrants lift and pick the list’s cells', async () => {
		const fetch = answer(200, rows([ROW], 21));
		const onview = vi.fn();
		render(EvalItems, { ...PAIR, onview });

		const square = screen.getByRole('img', { name: /^core: Only A right 0, Only B right 3/ });
		await expect.element(square).toBeVisible();
		const suites = asked(fetch).find((url) => url.pathname.endsWith('/items/suites'))!;
		expect(suites.pathname).toBe('/api/projects/acme/evals/runs/r1/items/suites');
		expect(Object.fromEntries(suites.searchParams)).toEqual({ with: 'acme/evals/r2' });

		// The top left quadrant is only B's: the pointer there lifts the list's button for it.
		const side = square.element().getBoundingClientRect().width;
		const onlyB = screen.getByRole('radio', { name: /Only B right/ });
		await userEvent.hover(square, { position: { x: side / 4, y: side / 4 } });
		await expect.element(onlyB).toHaveClass('text-foreground');
		await expect.element(screen.getByText('Only B right 3', { exact: true })).toBeVisible();

		// A click there shows that cell of the task shown (the suite holds arc_easy), page 1.
		await userEvent.click(square, { position: { x: side / 4, y: side / 4 } });
		await vi.waitFor(() => expect(lists(fetch).at(-1)!.searchParams.get('keep')).toBe('only_b'));
		const last = lists(fetch).at(-1)!.searchParams;
		expect([last.get('task'), last.get('offset')]).toEqual(['arc_easy', '0']);
		expect(onview).toHaveBeenLastCalledWith(
			expect.objectContaining({ task: 'arc_easy', keep: 'only_b', page: undefined })
		);
	});

	it('names each run in a refusal, even when one run’s path starts the other’s', async () => {
		answer(409, { message: 'run:acme/evals/r1-old is schema 10, run:acme/evals/r1 is 11' });
		render(EvalItems, { ...PAIR, other: 'acme/evals/r1-old' });
		await expect
			.element(screen.getByRole('alert'))
			.toHaveTextContent('No item text for arc_easy: B is schema 10, A is 11');
	});

	it('says why a run has no item text', async () => {
		answer(409, { message: 'run:acme/evals/r1 is schema 10, whose predictions hold no text' });
		render(EvalItems, PAIR);
		// Named as its column is.
		await expect
			.element(screen.getByRole('alert'))
			.toHaveTextContent(
				'No item text for arc_easy: A is schema 10, whose predictions hold no text'
			);
	});
});
