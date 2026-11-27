import { redirect } from '@sveltejs/kit';
import { adminActions } from '#lib/server/admin-settings.ts';
import { adminHref } from '#lib/settings.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = () => redirect(307, adminHref('organizations'));

export const actions = adminActions;
