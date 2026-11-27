import { loadOrg, orgActions } from '#lib/server/org-settings.ts';
import type { PageServerLoad } from './$types';

/** An org's settings: one page of the sections this viewer sees, the sidebar its contents. */
export const load: PageServerLoad = ({ depends, params }) => loadOrg({ depends, params });

export const actions = orgActions;
