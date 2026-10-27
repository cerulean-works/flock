/* eslint-disable svelte/prefer-svelte-reactivity -- URLs here are replaced, never changed in place */
import type { ActionResult, SubmitFunction } from '$app/forms';
import { vi } from 'vitest';

/**
 * Stand-ins for `$app/forms`, `$app/navigation` and `$app/state` in component specs, which run
 * without a SvelteKit app. A spec mocks the modules with these:
 *
 *     vi.mock('$app/forms', () => import('#lib/testing/app.svelte.ts'));
 *
 * `use:enhance` forms answer with `app.result` instead of posting, and record what they sent in
 * `app.sent`. `goto` moves `page.url` there.
 */
/** What an action answers: its data, or a refusal with `{ error }`. */
interface Answer {
	type: 'success' | 'failure';
	status: number;
	data?: Record<string, unknown>;
}

export const app = $state({
	/** What the next enhanced form's action answers. */
	result: { type: 'success', status: 200, data: {} } as Answer,
	/** Every enhanced form sent, in order: its action and fields. */
	sent: [] as { action: string; form: FormData }[]
});

export const page = $state({
	url: new URL('http://flock.test/'),
	data: {} as Record<string, unknown>,
	params: {} as Record<string, string>
});

/** Back to a page at `url`, with no forms sent and success as the answer. */
export function resetApp(url = 'http://flock.test/', data: Record<string, unknown> = {}) {
	page.url = new URL(url);
	page.data = data;
	app.result = { type: 'success', status: 200, data: {} };
	app.sent = [];
	goto.mockClear();
}

export const goto = vi.fn(async (to: string | URL) => {
	page.url = new URL(to, page.url);
});
export const invalidate = vi.fn(async () => {});
export const invalidateAll = vi.fn(async () => {});

export function enhance(form: HTMLFormElement, submit: SubmitFunction = () => {}) {
	async function onsubmit(event: SubmitEvent) {
		event.preventDefault();
		const formData = new FormData(form, event.submitter);
		const action = new URL(form.getAttribute('action') ?? '', page.url);
		let cancelled = false;
		const after = await submit({
			action,
			formData,
			formElement: form,
			controller: new AbortController(),
			submitter: event.submitter,
			cancel: () => (cancelled = true)
		});
		if (cancelled) return;
		app.sent.push({ action: action.pathname + action.search, form: formData });
		if (typeof after === 'function')
			await after({
				action,
				formData,
				formElement: form,
				result: app.result as ActionResult,
				update: async () => {}
			});
	}
	form.addEventListener('submit', onsubmit);
	return { destroy: () => form.removeEventListener('submit', onsubmit) };
}
