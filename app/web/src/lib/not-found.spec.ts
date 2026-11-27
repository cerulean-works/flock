import { describe, expect, it } from 'vitest';
import { missingHeading, missingTrail } from './not-found.ts';

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

	it('leads a settings page to its settings', () => {
		expect(labels('/orgs/acme/nope')).toEqual(['Settings /orgs/acme']);
		expect(labels('/orgs/gone')).toBeNull();
		expect(labels('/account/nope')).toEqual(['Account /account']);
		expect(labels('/admin/nope')).toEqual(['Super admin /admin']);
	});
});

describe('missingHeading', () => {
	it('names the thing missing, one way', () => {
		expect(missingHeading('folder -/a/b not found')).toBe('No folder at this address');
		expect(missingHeading('settings page nope not found')).toBe('No settings page at this address');
		expect(missingHeading('project gone not found')).toBe('No project at this address');
		expect(missingHeading('Not Found')).toBe('No page at this address');
		expect(missingHeading(undefined)).toBe('No page at this address');
	});
});
