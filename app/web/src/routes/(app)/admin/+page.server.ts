import { adminActions, loadAdmin } from '#lib/server/admin-settings.ts';
import type { PageServerLoad } from './$types';

/** The server's settings (super admins): one page of its sections. */
export const load: PageServerLoad = () => loadAdmin();

export const actions = adminActions;
