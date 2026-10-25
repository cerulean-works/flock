import { describe, expect, it } from 'vitest';
import { datasetHref, datasetsHref, withParam } from './url.ts';

describe('dataset URLs', () => {
	it('lists an org’s datasets', () => {
		expect(datasetsHref('flock')).toBe('/datasets/flock');
	});

	it('opens a dataset, with only the params given', () => {
		expect(datasetHref('flock', 'fineweb')).toBe('/datasets/flock/fineweb');
		expect(datasetHref('flock', 'fineweb', { v: 3, tab: null })).toBe(
			'/datasets/flock/fineweb?v=3'
		);
	});

	it('switches tabs in place', () => {
		const url = { pathname: '/datasets/flock/fineweb', search: '?v=2' };
		expect(withParam(url, 'tab', 'viewer')).toBe('/datasets/flock/fineweb?v=2&tab=viewer');
		expect(withParam(url, 'v', null)).toBe('/datasets/flock/fineweb');
	});
});
