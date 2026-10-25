import type { DatasetVisibility } from '#lib/types.ts';

/** Who can see a dataset, as the pages name it. */
export const VISIBILITY: Record<DatasetVisibility, { label: string; hint: string }> = {
	private: { label: 'Private', hint: 'Only members of the organization' },
	internal: { label: 'Internal', hint: 'Everyone signed in to this Flock' }
};
