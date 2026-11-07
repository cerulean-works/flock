import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { writeFilters } from './filters.ts';
import { leftWorkspace, rememberFilters, showingProject } from './remembered.ts';

const FILTERS = writeFilters([{ key: 'state', op: 'eq', value: 'running' }])!;
const OTHER = writeFilters([{ key: 'config.lr', op: 'lt', value: 0.001 }])!;

const url = (filters?: string) =>
	new URLSearchParams(filters ? { filters, sort: 'name' } : { sort: 'name' });

describe('rememberFilters', () => {
	beforeEach(() => {
		const items = new Map<string, string>();
		vi.stubGlobal('localStorage', {
			getItem: (k: string) => items.get(k) ?? null,
			setItem: (k: string, v: string) => void items.set(k, v),
			removeItem: (k: string) => void items.delete(k)
		});
		leftWorkspace();
	});
	afterEach(() => vi.unstubAllGlobals());

	it('brings the filters back when arriving at a project left filtered', () => {
		expect(rememberFilters('lab', 'sweep', url(FILTERS))).toBeNull();
		leftWorkspace();
		expect(rememberFilters('lab', 'sweep', url())).toBe(FILTERS);
	});

	it('remembers filters cleared while on the project', () => {
		rememberFilters('lab', 'sweep', url(FILTERS));
		expect(rememberFilters('lab', 'sweep', url())).toBeNull();
		leftWorkspace();
		expect(rememberFilters('lab', 'sweep', url())).toBeNull();
	});

	it('keeps each project its own filters', () => {
		rememberFilters('lab', 'sweep', url(FILTERS));
		rememberFilters('lab', 'pretrain', url(OTHER));
		expect(rememberFilters('lab', 'sweep', url())).toBe(FILTERS);
		expect(showingProject('lab', 'sweep')).toBe(true);
		rememberFilters('lab', 'pretrain', url(OTHER));
		leftWorkspace();
		expect(rememberFilters('lab', 'pretrain', url())).toBe(OTHER);
	});

	it('lets a link with filters win, and keeps those', () => {
		rememberFilters('lab', 'sweep', url(FILTERS));
		leftWorkspace();
		expect(rememberFilters('lab', 'sweep', url(OTHER))).toBeNull();
		leftWorkspace();
		expect(rememberFilters('lab', 'sweep', url())).toBe(OTHER);
	});

	it('drops malformed filters', () => {
		localStorage.setItem('flock:filters:lab/sweep', 'not json');
		expect(rememberFilters('lab', 'sweep', url())).toBeNull();
	});
});
