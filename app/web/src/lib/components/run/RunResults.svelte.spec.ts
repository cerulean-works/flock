import { afterEach, describe, expect, it, vi } from 'vitest';
import { page as screen } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../../routes/layout.css';
import ComparePage from '../../../routes/(app)/[entity]/[project]/runs/[run]/compare/+page.svelte';
import type { EvalResults, ItemDiff, RunDetail } from '#lib/types.ts';
import RunResults from './RunResults.svelte';

const run = (id: string, display_name: string | null = null) =>
	({ id, entity: 'acme', project: 'evals', display_name }) as RunDetail;

const RESULTS = {
	schema_version: 9,
	provenance: {
		model: 'allenai/OLMo-2-0425-1B@a1847dff',
		model_digest: null,
		flock_commit: '1fa70ea',
		command: 'flock eval run --suite base_easy',
		device: 'NVIDIA H200',
		dtype: 'bfloat16',
		batch_tokens: 16384,
		max_length: 4096,
		add_bos: false,
		attention: 'sdpa',
		sdpa_backend: 'EFFICIENT_ATTENTION',
		torch: '2.14.1',
		transformers: '5.20.1',
		tokenizers: '0.23.3',
		datasets: '5.1.0',
		python: '3.14.7',
		created_at: '2026-11-21T04:25:07+00:00'
	},
	suites: [
		{ suite: 'arc', suite_hash: 'a'.repeat(64), members: ['arc_easy'], metric: null, score: 0.5 }
	],
	tasks: [
		{
			task: 'tw_exam',
			task_hash: 'e'.repeat(64),
			dataset: {
				repo: 'cerulean-works/tw-mock-exams',
				revision: 'e62f9adf',
				subset: 'chemistry',
				split: 'test',
				private: true
			},
			primary_metric: 'acc_per_char',
			metrics: { acc_per_char: 0.17863397300243378, bpb: 2.4 },
			num_docs: 571,
			limit: null,
			truncation: { requests: 0, docs: 0, max_tokens_cut: 0 }
		}
	]
} as EvalResults;

// The items ask Flock through the dashboard; these tests answer that no run has text.
function noText() {
	const message = 'run:acme/evals/r1 is schema 10, whose predictions hold no text';
	vi.stubGlobal(
		'fetch',
		vi.fn(async () => new Response(JSON.stringify({ message }), { status: 409 }))
	);
}

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('the Results tab', () => {
	it('shows suites, each task’s primary metric with its hash and dataset, and the provenance', async () => {
		noText();
		render(RunResults, { run: run('r1'), results: RESULTS });
		const task = screen.getByRole('row').filter({ hasText: 'tw_exam' });
		await expect.element(task).toBeVisible();
		await expect.element(task.getByText('acc_per_char')).toBeVisible();
		await expect.element(task.getByText('571')).toBeVisible();
		await expect.element(task.getByText('eeeeeeeeeeee')).toBeVisible();
		await expect.element(task.getByLabelText('Private dataset')).toBeInTheDocument();
		await expect.element(screen.getByText('EFFICIENT_ATTENTION')).toBeVisible();
		await expect
			.element(screen.getByRole('row').filter({ hasText: 'arc' }).getByText('0.5'))
			.toBeVisible();
	});

	it('offers its items, wrong ones by default, counted from the accuracy and the docs', async () => {
		noText();
		render(RunResults, { run: run('r1'), results: { ...RESULTS, schema_version: 11 } });
		// 0.1786 of 571 docs right is 102.
		await expect.element(screen.getByRole('radio', { name: /Wrong/ })).toHaveTextContent('469');
		await expect.element(screen.getByRole('radio', { name: /Right/ })).toHaveTextContent('102');
		await expect.element(screen.getByRole('radio', { name: /All/ })).toHaveTextContent('571');
		await expect.element(screen.getByRole('alert')).toHaveTextContent(/schema 10/);
	});

	it('says how to read the items of a run scored before predictions held text', async () => {
		render(RunResults, { run: run('r1'), results: RESULTS });
		await expect.element(screen.getByText(/hold no text \(results schema 9\)/)).toBeVisible();
		expect(screen.getByRole('radio').all()).toHaveLength(0);
	});

	it('says which schemas it reads instead of guessing at another', async () => {
		noText();
		render(RunResults, { run: run('r1'), results: { ...RESULTS, schema_version: 13 } });
		await expect
			.element(screen.getByText(/has schema 13; the dashboard reads schemas\s+8, 9, 10, 11, 12\./))
			.toBeVisible();
	});
});

const DIFF: ItemDiff = {
	a: 'allenai/OLMo-2-0425-1B@a1847dff',
	b: 'meta-llama/Llama-3.2-1B@4e20de36',
	tasks: [
		{
			task: 'arc_easy',
			task_hash: 'h'.repeat(64),
			metric: 'acc_per_char',
			num_docs: 5,
			both_right: 1,
			both_wrong: 1,
			only_a: ['Mercury_1', 'Mercury_2'],
			only_b: ['Mercury_3']
		}
	],
	not_compared: [
		['paloma', 'not_accuracy'],
		['mine', 'only_a']
	],
	schema_version: 1
};

describe('the compare page', () => {
	it('shows the four counts per task, and the ids of a cell when the runs have no text', async () => {
		noText();
		render(ComparePage, {
			data: { a: run('r1', 'olmo'), b: run('r2'), diff: DIFF, refusal: null }
		} as never);
		const row = screen.getByRole('row').filter({ hasText: 'arc_easy' });
		expect(
			row
				.getByRole('cell')
				.all()
				.slice(1)
				.map((c) => c.element().textContent!.trim())
		).toEqual(['5', '1', '1', '2', '1']);
		// The items start on the first task with docs only A got right.
		await expect.element(screen.getByRole('alert')).toHaveTextContent(/schema 10/);
		await expect.element(screen.getByText('Mercury_1 Mercury_2')).toBeVisible();
		await screen.getByRole('radio', { name: /Only B right/ }).click();
		await expect.element(screen.getByText('Mercury_3')).toBeVisible();
		await expect.element(screen.getByText('Scored by A only')).toBeVisible();
		await expect.element(screen.getByRole('button', { name: /olmo/ })).toBeVisible();
	});

	it('says why a pair can’t be compared', async () => {
		const refusal = 'different instrument: provenance.dtype is float32 in a, bfloat16 in b';
		render(ComparePage, {
			data: { a: run('r1'), b: run('r2'), diff: null, refusal }
		} as never);
		await expect.element(screen.getByRole('alert')).toHaveTextContent(refusal);
	});
});
