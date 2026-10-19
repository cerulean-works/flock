import { FLOCK_PUBLIC_URL, FLOCK_URL } from '$app/env/private';
import type { LayoutServerLoad } from './$types';

// Reads only `locals`, so it runs when the document loads and isn't re-run by navigation.
export const load: LayoutServerLoad = ({ locals }) => ({
	user: locals.user,
	/** Flock as the wandb SDK reaches it, for setup instructions. */
	flockUrl: FLOCK_PUBLIC_URL ?? FLOCK_URL
});
