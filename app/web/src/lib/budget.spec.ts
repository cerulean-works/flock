import { describe, expect, it } from 'vitest';
import {
	deletedProjectName,
	deletedTotals,
	monthSpan,
	dismissedBy,
	warningText
} from './budget.ts';
import type { BudgetWarning, DeletedProject } from './types.ts';

const base = {
	month: '2026-10',
	fingerprint: 'f',
	dismissed: false,
	dismissed_by: null,
	dismissed_at: null
};

const unpriced: BudgetWarning = {
	...base,
	id: 'unpriced:2026-10',
	kind: 'unpriced',
	gpu_types: [
		{ gpu_type: 'H200', gpu_hours: 1520.5 },
		{ gpu_type: 'L40S', gpu_hours: 0.25 }
	]
};
const unknown: BudgetWarning = {
	...base,
	id: 'unknown_gpu:2026-10',
	kind: 'unknown_gpu',
	runs: 15,
	hours: 3.46,
	dismissed: true
};

function deleted(over: Partial<DeletedProject>): DeletedProject {
	return {
		id: 1,
		name: 'q',
		deleted_at: 1792310034,
		cost: 100,
		gpu_hours: 2,
		runs: 3,
		months: [{ month: '2026-10', cost: 100, gpu_hours: 2 }],
		excluded: false,
		excluded_at: null,
		excluded_by: null,
		...over
	};
}

describe('budget warnings', () => {
	it('say what they are about', () => {
		expect(warningText(unpriced)).toBe(
			'No price for H200 (1,521 h), L40S (0.3 h): counted as free.'
		);
		expect(warningText(unknown)).toBe('15 runs (3.5 h) with unknown GPUs.');
		expect(warningText({ ...unknown, runs: 1 })).toBe('1 run (3.5 h) with unknown GPUs.');
	});

	it('say who dismissed them and when', () => {
		const when = (ts: number) => `at ${ts}`;
		const by = { id: 1, name: 'Ana', email: 'ana@example.com', avatar_url: null };
		expect(dismissedBy({ ...unknown, dismissed_by: by, dismissed_at: 5 }, when)).toBe(
			'Dismissed by Ana, at 5'
		);
		expect(dismissedBy({ ...unknown, dismissed_at: 5 }, when)).toBe('Dismissed, at 5');
		expect(dismissedBy(unknown, when)).toBe('Dismissed');
	});
});

describe('deleted projects', () => {
	it('are named, or stand for the ones whose names are gone', () => {
		expect(deletedProjectName(deleted({}))).toBe('q');
		expect(deletedProjectName(deleted({ name: null }))).toBe(
			'Projects deleted earlier (names not kept)'
		);
	});

	it('span the months they spent in', () => {
		expect(monthSpan([])).toBe('');
		expect(monthSpan([{ month: '2026-10' }])).toBe('October 2026');
		expect(monthSpan([{ month: '2026-08' }, { month: '2026-10' }])).toBe(
			'August 2026 – October 2026'
		);
	});

	it('add up what is counted and what is left out', () => {
		const projects = [
			deleted({ cost: 100 }),
			deleted({ id: 2, cost: 40, excluded: true }),
			deleted({ id: 3, cost: 2.5, excluded: true })
		];
		expect(deletedTotals(projects)).toEqual({ counted: 100, excluded: 42.5 });
	});
});
