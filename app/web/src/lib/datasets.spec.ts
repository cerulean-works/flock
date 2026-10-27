import { describe, expect, it } from 'vitest';
import {
	cellCut,
	cellText,
	crumbs,
	datasetUri,
	datasetsApi,
	datasetsHref,
	daysLeft,
	fileHref,
	nameOf,
	parentOf,
	pathError,
	tagError,
	writeSnippet
} from './datasets.ts';

describe('dataset URLs', () => {
	it('encodes each part of a path, keeping its slashes', () => {
		expect(datasetsHref('acme')).toBe('/datasets/acme');
		expect(datasetsHref('acme', 'eval/mmlu')).toBe('/datasets/acme/eval/mmlu');
		expect(datasetsHref('acme', 'a b/#1')).toBe('/datasets/acme/a%20b/%231');
		expect(fileHref('acme', 'eval/README.md')).toBe('/storage/acme/flock-datasets/eval/README.md');
		expect(datasetUri('eval/mmlu')).toBe('s3://flock-datasets/eval/mmlu');
	});

	it('has breadcrumbs inside a folder or dataset, none at the root', () => {
		expect(crumbs('acme', 'Acme Lab', '')).toEqual([]);
		expect(crumbs('acme', 'Acme Lab', 'eval/mmlu')).toEqual([
			{ label: 'Datasets', href: '/datasets/acme' },
			{ label: 'Acme Lab', href: '/datasets/acme' },
			{ label: 'eval', href: '/datasets/acme/eval' },
			{ label: 'mmlu', href: '/datasets/acme/eval/mmlu' }
		]);
	});

	it('names parents and leaves', () => {
		expect(parentOf('eval/deep/mmlu')).toBe('eval/deep');
		expect(parentOf('mmlu')).toBe('');
		expect(nameOf('eval/deep/mmlu')).toBe('mmlu');
	});

	it('builds the dashboard endpoints with repeated parameters', () => {
		expect(datasetsApi('acme', 'rows', { path: 'a/b', offset: 0, columns: ['x', 'y'] })).toBe(
			'/api/datasets/acme/rows?path=a%2Fb&offset=0&columns=x&columns=y'
		);
		expect(datasetsApi('acme', 'versions', { path: 'a', cursor: null })).toBe(
			'/api/datasets/acme/versions?path=a'
		);
	});
});

describe('names the API takes', () => {
	it('checks new paths as the API does (D95)', () => {
		expect(pathError('eval/mmlu-v2.1_x')).toBeNull();
		expect(pathError('')).toBe('Enter a path');
		for (const bad of ['a//b', '/a', 'a/', 'sp ace', 'é', 'a/../b', 'a/.']) {
			expect(pathError(bad), bad).not.toBeNull();
		}
		expect(pathError('a/_versions')).toBe('A folder can’t be named _versions');
		expect(pathError('x'.repeat(513))).toBe('Use at most 512 characters');
	});

	it('checks tag names (D96)', () => {
		expect(tagError('v1.0')).toBeNull();
		expect(tagError('x'.repeat(100))).toBeNull();
		expect(tagError('x'.repeat(101))).toBe('Use at most 100 characters');
		for (const bad of ['ü', '.hidden', '-x', 'a b']) expect(tagError(bad), bad).not.toBeNull();
		expect(tagError('123')).toBe('Add a letter, since numbers alone are versions');
	});
});

describe('display', () => {
	it('counts whole days left in the trash', () => {
		const now = 1_790_000_000;
		expect(daysLeft(now + 14 * 86_400, now)).toBe(14);
		expect(daysLeft(now + 86_400 + 1, now)).toBe(2);
		expect(daysLeft(now + 60, now)).toBe(1);
		expect(daysLeft(now - 60, now)).toBe(0);
	});

	it('reads every kind of cell as text', () => {
		expect(cellText({ k: 'null' })).toBe('null');
		expect(cellText({ k: 'num', v: 3, label: 'dog' })).toBe('3 (dog)');
		expect(cellText({ k: 'image', mime: 'image/png', size: 2048, data: '' })).toBe(
			'PNG image, 2.0 KB'
		);
		expect(cellText({ k: 'blob', size: 10 })).toBe('blob, 10 B');
		expect(cellText({ k: 'vec', dtype: 'float', len: 768, head: [] })).toBe('float[768]');
		expect(cellCut({ k: 'str', v: 'abc', len: 600 })).toBe(true);
		expect(cellCut({ k: 'str', v: 'abc' })).toBe(false);
	});

	it('writes the snippet for the org', () => {
		expect(writeSnippet('acme')).toContain('s3.credentials("acme")');
		expect(writeSnippet('acme')).toContain('creds.uri("flock-datasets", "eval", "mmlu")');
	});
});
