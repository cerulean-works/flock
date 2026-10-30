import { describe, expect, it } from 'vitest';
import {
	evalMetrics,
	folderHref,
	isCheckpoint,
	loadSnippet,
	modelApi,
	modelsApi,
	modelsHref,
	pullSnippet,
	runPathHref,
	sortTags,
	tagError,
	versionHref,
	versionRef
} from './models.ts';
import type { ModelEvalRun } from './types.ts';

describe('model URLs', () => {
	it('links the list, a model, a version and its folder', () => {
		expect(modelsHref('acme')).toBe('/models/acme');
		expect(modelsHref('acme', 'olmo2-1b')).toBe('/models/acme/olmo2-1b');
		expect(versionHref('acme', 'olmo2-1b', 12)).toBe('/models/acme/olmo2-1b?version=12');
		expect(versionHref('acme', 'olmo2-1b', 'release', 'files')).toBe(
			'/models/acme/olmo2-1b?version=release&tab=files'
		);
		expect(folderHref('acme', 'olmo2-1b/u/01JD/')).toBe(
			'/storage/acme/flock-models/olmo2-1b/u/01JD/'
		);
		expect(runPathHref('acme/evals/e1')).toBe('/acme/evals/runs/e1');
		expect(runPathHref('acme/evals')).toBeNull();
	});

	it('names the dashboard’s endpoints', () => {
		expect(modelsApi('acme', '-/trash')).toBe('/api/models/acme/-/trash');
		expect(modelApi('acme', 'olmo2-1b')).toBe('/api/models/acme/olmo2-1b');
		expect(modelApi('acme', 'olmo2-1b', 'versions', { before: 50, checkpoints: null })).toBe(
			'/api/models/acme/olmo2-1b/versions?before=50'
		);
	});
});

describe('tags', () => {
	it('knows checkpoint tags', () => {
		expect(isCheckpoint('checkpoint-12000')).toBe(true);
		expect(isCheckpoint('checkpoint-')).toBe(false);
		expect(isCheckpoint('checkpoint-best')).toBe(false);
	});

	it('sorts other tags by name, then checkpoint tags by step', () => {
		expect(sortTags(['checkpoint-12000', 'release', 'checkpoint-900', 'best'])).toEqual([
			'best',
			'release',
			'checkpoint-900',
			'checkpoint-12000'
		]);
		expect(sortTags([{ name: 'checkpoint-20' }, { name: 'checkpoint-3' }])).toEqual([
			{ name: 'checkpoint-3' },
			{ name: 'checkpoint-20' }
		]);
	});

	it('refuses primary and the dataset tag rules', () => {
		expect(tagError('release')).toBeNull();
		expect(tagError('primary')).toBe('That name is for the primary version, so it can’t be a tag');
		expect(tagError('Primary')).toMatch(/primary version/);
		expect(tagError('12')).toMatch(/numbers alone/);
		expect(tagError('-x')).toMatch(/starting with a letter or digit/);
	});
});

describe('display', () => {
	it('writes the load and pull snippets for a version', () => {
		const ref = versionRef('acme', 'olmo2-1b', 12);
		expect(ref).toBe('acme/olmo2-1b@12');
		expect(loadSnippet(ref)).toBe(
			'from flock.hub import models\n\nmodel = models.load_from_hub("acme/olmo2-1b@12", device="cuda")'
		);
		expect(pullSnippet(ref)).toBe('flock models pull acme/olmo2-1b@12');
	});

	it('takes the eval metrics from every run, leaving out throughput and wandb’s own', () => {
		const run = (metrics: Record<string, number>) => ({ metrics }) as ModelEvalRun;
		expect(
			evalMetrics([
				run({ 'mmlu/acc': 1, 'mmlu/docs_per_second': 2, _runtime: 3 }),
				run({ 'arc/acc': 1, 'suites/core': 0.5 })
			])
		).toEqual(['arc/acc', 'mmlu/acc', 'suites/core']);
	});
});
