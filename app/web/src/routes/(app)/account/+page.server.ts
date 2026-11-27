import { accountActions, loadAccount } from '#lib/server/account-settings.ts';
import type { PageServerLoad } from './$types';

/** The account: one page of its sections, the settings sidebar its contents. */
export const load: PageServerLoad = ({ locals }) => loadAccount({ locals });

// Forms that post here (`/account?/createKey`, from any page) keep working.
export const actions = accountActions;
