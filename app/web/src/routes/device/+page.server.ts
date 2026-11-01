import { fail, isHttpError, redirect } from '@sveltejs/kit';
import { change, flock, refused } from '#lib/server/flock.ts';
import type { DeviceRequest } from '#lib/types.ts';
import { formText } from '#lib/utils.ts';
import type { Actions, PageServerLoad } from './$types';

/**
 * Approving a `flock login`, in three steps: enter the code the terminal shows (prefilled from
 * `?code=` when the CLI opened this page), confirm the machine, done. Signed-out visitors sign
 * in first and come back here (hooks.server.ts). Only a browser session can approve: Flock
 * refuses API keys and CLI tokens here.
 */

const devicePath = (code: string) => `/api/v1/auth/device/${encodeURIComponent(code)}`;

const UNKNOWN =
	'That code is wrong, has expired or was already used. Check it, or run flock login again.';

type Step =
	| { step: 'enter'; code: string; error: string | null }
	| { step: 'confirm'; request: DeviceRequest; now: number }
	| { step: 'done'; approved: boolean };

export const load: PageServerLoad = async ({ url }): Promise<Step> => {
	const done = url.searchParams.get('done');
	if (done === 'approved' || done === 'denied')
		return { step: 'done', approved: done === 'approved' };
	const code = url.searchParams.get('code')?.trim() ?? '';
	if (!code) return { step: 'enter', code, error: null };
	try {
		const res = await flock(devicePath(code));
		return {
			step: 'confirm',
			request: (await res.json()) as DeviceRequest,
			now: Date.now() / 1000
		};
	} catch (e) {
		// Unknown codes count against the user: past ten in ten minutes, Flock answers 429.
		if (isHttpError(e) && e.status === 404) return { step: 'enter', code, error: UNKNOWN };
		if (isHttpError(e) && e.status === 429) return { step: 'enter', code, error: e.body.message };
		throw e;
	}
};

export const actions: Actions = {
	/** Authorize (`approve=true`) or cancel, which denies the sign-in. */
	default: async ({ request }) => {
		const form = await request.formData();
		const code = formText(form, 'code');
		const approve = form.get('approve') === 'true';
		if (!code) return fail(400, { error: 'Enter the code your terminal shows' });
		const res = await change('POST', devicePath(code), { approve });
		if (refused(res)) return res.status === 404 ? fail(404, { error: UNKNOWN }) : res;
		redirect(303, `/device?done=${approve ? 'approved' : 'denied'}`);
	}
};
