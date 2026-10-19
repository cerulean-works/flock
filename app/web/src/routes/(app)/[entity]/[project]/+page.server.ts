import { runActions } from '#lib/server/runs.ts';
import type { Actions } from './$types';

// Run changes. The page's data comes from the universal load (+page.ts); these only write.
export const actions: Actions = runActions;
