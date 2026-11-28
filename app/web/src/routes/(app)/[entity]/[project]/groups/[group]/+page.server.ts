import { runActions } from '#lib/server/runs.ts';
import type { Actions } from './$types';

// Run changes from the group's runs (Edit run, tags, move, delete), as on the project's: the
// page's data comes from the universal load (+page.ts); these only write.
export const actions: Actions = runActions;
