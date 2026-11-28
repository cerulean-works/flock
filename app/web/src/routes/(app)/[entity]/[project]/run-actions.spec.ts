import { describe, expect, it } from 'vitest';
import { runActions } from '#lib/server/runs.ts';

// Every page that shows runs posts their changes (`?/edit`, `?/tag`…) to itself: one without
// the actions answers 405 Method Not Allowed.
describe('pages with runs take the run actions', () => {
	it.each([
		['project', () => import('./+page.server.ts')],
		['group', () => import('./groups/[group]/+page.server.ts')],
		['run', () => import('./runs/[run]/+page.server.ts')]
	])('%s', async (_, page) => {
		const { actions } = await page();
		expect(Object.keys(actions).sort()).toEqual(Object.keys(runActions).sort());
	});
});
