import { afterEach, describe, expect, it, vi } from 'vitest';
import { page as screen } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../../routes/layout.css';
import type { EvalItem, ItemsPage } from '#lib/types.ts';
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

/** The dashboard's answer to every request; the URLs asked for are in its calls. */
function answer(status: number, body: unknown) {
	const fetch = vi.fn(async () => new Response(JSON.stringify(body), { status }));
	vi.stubGlobal('fetch', fetch);
	return fetch;
}

const asked = (fetch: ReturnType<typeof answer>, call: number) =>
	new URL(String((fetch.mock.calls[call] as unknown[])[0]), 'https://flock.test');

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
		const page: ItemsPage = { total: 21, offset: 0, items: [ITEM] };
		const fetch = answer(200, page);
		render(EvalItems, PAIR);

		// The first task with docs in the first keep, asked of the pair, surest gap first.
		const row = screen.getByRole('row').filter({ hasText: 'Mercury_7017010' });
		await expect.element(row).toBeVisible();
		const url = asked(fetch, 0);
		expect(url.pathname).toBe('/api/projects/acme/evals/runs/r1/items');
		expect(Object.fromEntries(url.searchParams)).toEqual({
			task: 'arc_easy',
			keep: 'only_a',
			sort: 'margin',
			offset: '0',
			limit: '20',
			with: 'acme/evals/r2'
		});
		// Margins: A log(0.4 / 0.3), B log(0.3 / 0.5); the gap between them.
		expect(
			row
				.getByRole('cell')
				.all()
				.slice(2)
				.map((c) => c.element().textContent!.replace(/\s+/g, ' ').trim())
		).toEqual(['Mercury', 'right: Mercury +0.29', 'wrong: Mars −0.51', '+0.80', '']);
		// The task's scores and flips, from the counts.
		await expect.element(screen.getByText(/docs only A got right/)).toBeVisible();

		await row.getByRole('button', { name: 'Mercury_7017010' }).click();
		// The whole question in the row, then a row per choice in the same table.
		await expect.element(row.getByText(/Answer:/)).toBeVisible();
		const choices = screen.getByRole('table');
		// Each run's pick is named in its cell: A's on the gold choice, B's on Mars.
		const gold = choices.getByRole('row').filter({ hasText: 'gold' });
		await expect.element(gold.getByRole('cell', { name: /^A \(its pick\)/ })).toBeVisible();
		const mars = choices.getByRole('row').filter({ hasText: 'Mars' });
		await expect.element(mars.getByRole('cell', { name: /^B \(its pick\)/ })).toBeVisible();
		await expect
			.element(screen.getByRole('cell', { name: /^B \(its pick\)/ }))
			.toHaveAccessibleName('B (its pick): share 50.0% · prob 66.5% · logprob -1.00');
	});

	it('switches to the dataset’s order', async () => {
		const fetch = answer(200, { total: 1, offset: 0, items: [ITEM] });
		render(EvalItems, PAIR);
		await screen.getByRole('button', { name: 'Biggest margin gap first' }).click();
		await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
		expect(asked(fetch, 1).searchParams.get('sort')).toBe('doc');
		await expect.element(screen.getByRole('button', { name: 'Dataset order' })).toBeVisible();
	});

	it('pages through a keep and starts over on another one', async () => {
		const page: ItemsPage = { total: 21, offset: 0, items: [ITEM] };
		const fetch = answer(200, page);
		render(EvalItems, PAIR);

		// Clicking the keep already chosen keeps the pager.
		await screen.getByRole('radio', { name: /Only A right/ }).click();
		await screen.getByRole('button', { name: 'Next page' }).click();
		await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
		expect(asked(fetch, 1).searchParams.get('offset')).toBe('20');
		await screen.getByRole('radio', { name: /Only B right/ }).click();
		await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(3));
		expect(asked(fetch, 2).searchParams.get('keep')).toBe('only_b');
		expect(asked(fetch, 2).searchParams.get('offset')).toBe('0');
		await expect
			.element(screen.getByRole('radio', { name: /Only B right/ }))
			.toHaveTextContent('4');
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
