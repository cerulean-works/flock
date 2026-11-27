import { describe, expect, it } from 'vitest';
import { refusalSentence } from './eval-refusal.ts';

const names = { 'acme/lm/a1': 'olmo-1b eval', 'acme/lm/b2': 'olmo-7b eval' };

describe('refusalSentence', () => {
	it('names the run without results', () => {
		expect(refusalSentence('run:acme/lm/b2 has no results.json', names)).toBe(
			'olmo-7b eval has no eval results.'
		);
	});

	it('says the settings differ', () => {
		const reason =
			"different instrument: provenance.harness is 'a' in run:acme/lm/a1, 'b' in run:acme/lm/b2";
		expect(refusalSentence(reason, names)).toBe(
			'These runs were evaluated with different settings, so their items can’t be paired.'
		);
	});

	it('says the docs differ', () => {
		const reason =
			'hellaswag has the same hash in run:acme/lm/a1 and run:acme/lm/b2 but other doc ids, or another order';
		expect(refusalSentence(reason, names)).toMatch(/list a task’s docs differently/);
	});

	it('names a run whose predictions disagree with its results', () => {
		const reason = 'run:acme/lm/a1: hellaswag has 10 prediction rows for 12 docs';
		expect(refusalSentence(reason, names)).toBe('olmo-1b eval has eval results that don’t add up.');
	});

	it('falls back to a general sentence', () => {
		expect(refusalSentence('Flock responded 418', names)).toBe(
			'These runs can’t be compared item by item.'
		);
		expect(refusalSentence('run:x/y/z has no results.json', names)).toBe(
			'One of the runs has no eval results.'
		);
	});
});
