import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { DatasetFolder } from '#lib/types.ts';
import { withDetails } from './datasets.ts';
import { flockAnswer } from './flock.ts';

vi.mock('./flock.ts', () => ({ flockAnswer: vi.fn() }));

beforeEach(() => vi.resetAllMocks());

const folder = (paths: string[], version: number | null = null): DatasetFolder => ({
	kind: 'folder',
	path: 'eval',
	datasets: paths.map((path) => ({ name: path, path, version, updated_at: 1 })),
	folders: [{ name: 'deep', path: 'eval/deep' }],
	files: [],
	cursor: 'next'
});

describe('a folder’s datasets with their details', () => {
	it('fills in sizes, and versions the listing has none of', async () => {
		vi.mocked(flockAnswer).mockImplementation(async (url) => {
			const path = new URLSearchParams(url.split('?')[1]).get('path')!;
			if (path === 'broken') return Response.json({ error: 'no' }, { status: 502 });
			return Response.json({ version: 4, data_size: path.length * 100 });
		});
		const got = await withDetails('acme', folder(['old', 'broken']));
		expect(got.datasets).toEqual([
			{ name: 'old', path: 'old', version: 4, updated_at: 1, size: 300 },
			{ name: 'broken', path: 'broken', version: null, updated_at: 1, size: null }
		]);
		expect(got.folders).toEqual(folder([]).folders);
		expect(got.cursor).toBe('next');
		expect(vi.mocked(flockAnswer).mock.calls.map(([url]) => url)).toEqual([
			'/api/v1/orgs/acme/datasets/info?path=old',
			'/api/v1/orgs/acme/datasets/info?path=broken'
		]);
	});

	it('keeps the version the listing has', async () => {
		vi.mocked(flockAnswer).mockResolvedValue(Response.json({ version: 9, data_size: 1 }));
		const got = await withDetails('acme', folder(['mmlu'], 12));
		expect(got.datasets[0]).toMatchObject({ version: 12, size: 1 });
	});

	it('reads a few at a time', async () => {
		let open = 0;
		let most = 0;
		vi.mocked(flockAnswer).mockImplementation(async () => {
			most = Math.max(most, ++open);
			await new Promise((r) => setTimeout(r, 5));
			open--;
			return Response.json({ version: 1, data_size: 1 });
		});
		const paths = Array.from({ length: 30 }, (_, i) => `d${i}`);
		const got = await withDetails('acme', folder(paths));
		expect(got.datasets.map((d) => d.path)).toEqual(paths);
		expect(most).toBe(8);
	});
});
