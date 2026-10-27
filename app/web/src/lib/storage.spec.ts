import { describe, expect, it } from 'vitest';
import {
	clickSelect,
	crumbs,
	entries,
	folderNameError,
	keyOfPath,
	nameOf,
	objectHref,
	parentOf,
	prettyJson,
	previewOf,
	s3Uri,
	typeLabel
} from './storage.ts';

describe('storage URLs', () => {
	it('encodes each part of a key, keeping its slashes', () => {
		expect(objectHref('acme', 'data')).toBe('/storage/acme/data');
		expect(objectHref('acme', 'data', 'runs/')).toBe('/storage/acme/data/runs/');
		expect(objectHref('acme', 'data', 'a b/#1?.txt')).toBe('/storage/acme/data/a%20b/%231%3F.txt');
	});
	it('reads a folder from a path ending in /', () => {
		expect(keyOfPath('', '/storage/acme/data')).toBe('');
		expect(keyOfPath('runs', '/storage/acme/data/runs/')).toBe('runs/');
		expect(keyOfPath('runs/a.txt', '/storage/acme/data/runs/a.txt')).toBe('runs/a.txt');
	});
	it('makes breadcrumbs with a link for every level', () => {
		expect(crumbs('acme', 'Acme Lab').map((c) => c.label)).toEqual(['Storage', 'Acme Lab']);
		expect(crumbs('acme', 'Acme Lab', 'data', 'runs/v1/model.pt')).toEqual([
			{ label: 'Storage', href: '/storage/acme' },
			{ label: 'Acme Lab', href: '/storage/acme' },
			{ label: 'data', href: '/storage/acme/data' },
			{ label: 'runs', href: '/storage/acme/data/runs/' },
			{ label: 'v1', href: '/storage/acme/data/runs/v1/' },
			{ label: 'model.pt', href: '/storage/acme/data/runs/v1/model.pt' }
		]);
		expect(crumbs('acme', 'Acme', 'data', 'runs/').map((c) => c.href)).toEqual([
			'/storage/acme',
			'/storage/acme',
			'/storage/acme/data',
			'/storage/acme/data/runs/'
		]);
	});
	it('names keys and their folders', () => {
		expect(nameOf('a/b/c.txt')).toBe('c.txt');
		expect(nameOf('a/b/')).toBe('b');
		expect(parentOf('a/b/c.txt')).toBe('a/b/');
		expect(parentOf('a/b/')).toBe('a/');
		expect(parentOf('c.txt')).toBe('');
		expect(s3Uri('data', 'runs/')).toBe('s3://data/runs/');
	});
});

describe('types and previews', () => {
	it('labels types by extension', () => {
		expect(typeLabel('runs/')).toBe('Folder');
		expect(typeLabel('plot.PNG')).toBe('PNG image');
		expect(typeLabel('config.json')).toBe('JSON');
		expect(typeLabel('README.md')).toBe('Markdown');
		expect(typeLabel('train.log')).toBe('LOG');
		expect(typeLabel('Makefile')).toBe('File');
	});
	it('previews by content type, then by extension', () => {
		expect(previewOf('x.bin', 'image/png')).toEqual({ kind: 'image', type: 'image/png' });
		expect(previewOf('paper.pdf', 'binary/octet-stream')).toEqual({
			kind: 'pdf',
			type: 'application/pdf'
		});
		expect(previewOf('notes', 'text/plain; charset=utf-8')).toEqual({ kind: 'text' });
		expect(previewOf('a.json', null)).toEqual({ kind: 'json' });
		expect(previewOf('a.md', 'application/octet-stream')).toEqual({ kind: 'markdown' });
		expect(previewOf('a.yaml', null)).toEqual({ kind: 'text' });
		expect(previewOf('clip.mp4', null)).toEqual({ kind: 'video', type: 'video/mp4' });
		expect(previewOf('a.parquet', null)).toEqual({ kind: null });
	});
	it('shows SVG only as an image', () => {
		expect(previewOf('evil.svg', 'image/svg+xml')).toEqual({
			kind: 'image',
			type: 'image/svg+xml'
		});
		expect(previewOf('evil.svg', null)).toEqual({ kind: 'image', type: 'image/svg+xml' });
	});
	it('never previews HTML as a page', () => {
		expect(previewOf('index.html', 'text/html')).toEqual({ kind: 'text' });
	});
	it('pretty-prints JSON that parses', () => {
		expect(prettyJson('{"a":[1,2]}')).toBe('{\n  "a": [\n    1,\n    2\n  ]\n}');
		expect(prettyJson('{"a":')).toBeNull();
	});
});

describe('the listing', () => {
	it('puts folders first', () => {
		const rows = entries(['b/'], [{ key: 'a.txt', size: 1, last_modified: 0, etag: '' }]);
		expect(rows.map((r) => [r.kind, r.key])).toEqual([
			['folder', 'b/'],
			['object', 'a.txt']
		]);
	});
	it('selects with clicks and shift-click ranges', () => {
		const keys = ['a/', 'b', 'c', 'd', 'e'];
		let selected = clickSelect(new Set(), keys, 'b', null, false);
		expect([...selected]).toEqual(['b']);
		selected = clickSelect(selected, keys, 'e', 'b', true);
		expect([...selected].sort()).toEqual(['b', 'c', 'd', 'e']);
		// Shift-clicking a selected row clears the range.
		selected = clickSelect(selected, keys, 'c', 'e', true);
		expect([...selected]).toEqual(['b']);
		// Without an anchor, shift-click toggles one row.
		expect([...clickSelect(new Set(), keys, 'a/', null, true)]).toEqual(['a/']);
	});
	it('checks folder names', () => {
		expect(folderNameError('')).toBe('Enter a name');
		expect(folderNameError('a/b')).toMatch(/without \//);
		expect(folderNameError('checkpoints')).toBeNull();
	});
});
