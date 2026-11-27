import { describe, expect, it } from 'vitest';
import { missingTrail } from './not-found.ts';

const labels = (path: string) => missingTrail(path)?.map((c) => `${c.label} ${c.href}`) ?? null;

describe('missingTrail', () => {
	it('stops at the last part above the missing one, past a `-`', () => {
		expect(labels('/datasets/acme/-/gone')).toEqual(['Datasets /datasets/acme']);
		expect(labels('/datasets/acme/pretrain/gone')).toEqual([
			'Datasets /datasets/acme',
			'pretrain /datasets/acme/pretrain'
		]);
		expect(labels('/storage/acme/raw/a%20b/gone.txt')).toEqual([
			'Storage /storage/acme',
			'raw /storage/acme/raw',
			'a b /storage/acme/raw/a%20b'
		]);
	});

	it('names a project’s page as its sidebar does, the org and project leading by themselves', () => {
		expect(labels('/acme/pretrain/runs/gone')).toEqual(['Runs /acme/pretrain/runs']);
		expect(labels('/acme/pretrain/groups/7/gone')).toEqual([
			'Groups /acme/pretrain/groups',
			'7 /acme/pretrain/groups/7'
		]);
	});

	it('knows nothing above an org, a project or a tab', () => {
		expect(labels('/gone')).toBeNull();
		expect(labels('/acme/gone')).toBeNull();
		expect(labels('/acme/pretrain/gone')).toBeNull();
		expect(labels('/datasets/gone')).toBeNull();
	});
});
