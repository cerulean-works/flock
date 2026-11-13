import { describe, expect, it } from 'vitest';
import {
	datasetCommand,
	defaultFolder,
	defaultModelName,
	endedText,
	hfApi,
	importsAt,
	progressText,
	repoError
} from './hf.ts';
import type { HFImport } from './types.ts';

const job = (fields: Partial<HFImport> = {}): HFImport => ({
	id: 1,
	kind: 'dataset',
	repo: 'acme/data',
	revision: 'main',
	sha: null,
	dest: 'web/data',
	state: 'running',
	phase: 'downloading',
	bytes_total: 4 * 1024 * 1024,
	bytes_done: 1024 * 1024,
	files_total: 4,
	files_done: 1,
	rows_done: 0,
	progress: 0.125,
	result: null,
	error: null,
	command: 'flock datasets import acme/data web/data --org acme',
	started_by: null,
	started_at: 1_790_000_000,
	finished_at: null,
	...fields
});

describe('Hugging Face imports', () => {
	it('names the dashboard’s endpoints', () => {
		expect(hfApi('acme')).toBe('/api/imports/acme/hf');
		expect(hfApi('acme', '12')).toBe('/api/imports/acme/hf/12');
		expect(hfApi('acme', 'preview', { kind: 'model', repo: 'Qwen/Qwen3-0.6B', x: null })).toBe(
			'/api/imports/acme/hf/preview?kind=model&repo=Qwen%2FQwen3-0.6B'
		);
	});

	it('checks repositories as the API does', () => {
		expect(repoError('Qwen/Qwen3-0.6B')).toBeNull();
		expect(repoError('')).toBe('Enter a repository');
		expect(repoError('qwen')).toBe('Enter the repository as owner/name');
		expect(repoError('a/b/c')).toBe('Enter the repository as owner/name');
		expect(repoError('../x')).toBe('Enter the repository as owner/name');
	});

	it('fills in the folder and the model name from the repository', () => {
		expect(defaultFolder('', 'HuggingFaceFW/fineweb-edu')).toBe('fineweb-edu');
		expect(defaultFolder('web', 'HuggingFaceFW/fineweb-edu')).toBe('web/fineweb-edu');
		expect(defaultModelName('Qwen/Qwen3-0.6B')).toBe('qwen3-0.6b');
		expect(defaultModelName('a/_Odd Name!')).toBe('odd-name-');
	});

	it('writes the flock command as the API does', () => {
		expect(datasetCommand('acme', 'a/b', 'web/b', 'default', null)).toBe(
			'flock datasets import a/b web/b --config default --org acme'
		);
		expect(datasetCommand('acme', 'a/b', 'my data', 'en', ['train', 'test'])).toBe(
			"flock datasets import a/b 'my data' --config en --split train --split test --org acme"
		);
	});

	it('shows a dataset’s imports in the folder it lands in, and every model’s', () => {
		const imports = [
			job({ id: 1, dest: 'web/data' }),
			job({ id: 2, dest: 'data' }),
			job({ id: 3, kind: 'model', dest: 'qwen' })
		];
		expect(importsAt(imports, 'dataset', 'web').map((i) => i.id)).toEqual([1]);
		expect(importsAt(imports, 'dataset').map((i) => i.id)).toEqual([2]);
		expect(importsAt(imports, 'model', 'web').map((i) => i.id)).toEqual([3]);
	});

	it('says how far an import is, in plain sentences', () => {
		expect(progressText(job())).toBe('Downloading. 1.0 MB of 4.0 MB');
		expect(progressText(job({ phase: 'converting' }))).toBe('Converting to Lance. 1 of 4 files');
		expect(progressText(job({ phase: 'committing' }))).toBe('Checking the files');
		expect(endedText(job({ state: 'finished' }))).toBe('Imported acme/data.');
		expect(
			endedText(job({ state: 'finished', result: { model: 'q', version: 3, existing: true } }))
		).toBe('acme/data was already imported as version 3.');
		expect(endedText(job({ state: 'failed' }))).toBe('The import of acme/data failed.');
		expect(endedText(job({ state: 'interrupted' }))).toBe(
			'The import of acme/data stopped when the server restarted. Start it again.'
		);
		for (const state of ['finished', 'failed', 'cancelled', 'interrupted'] as const) {
			expect(endedText(job({ state }))).not.toMatch(/[;:—–]/);
		}
	});
});
