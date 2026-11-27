import { redirect } from '@sveltejs/kit';
import { accountActions } from '#lib/server/account-settings.ts';
import { accountHref } from '#lib/settings.ts';
import type { PageServerLoad } from './$types';

/** The account opens on its profile; an old link's `#keys` survives and is followed from there. */
export const load: PageServerLoad = () => redirect(307, accountHref('profile'));

// Forms that post here (`/account?/createKey`, from any page) keep working.
export const actions = accountActions;
