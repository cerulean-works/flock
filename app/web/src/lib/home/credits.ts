/** Service lines the credit dashboard splits spend into, as its balance card names them. */
export const CREDIT_SERVICES = [
	{ key: 'HPC', label: 'HPC tasks' },
	{ key: 'Other', label: 'Other' },
	{ key: 'HFS', label: 'HFS storage' },
	{ key: 'VDS', label: 'Virtual disks' },
	{ key: 'VCS', label: 'Virtual machines' }
] as const;

export type CreditService = (typeof CREDIT_SERVICES)[number]['key'];

/** The NCHC wallet as the credit dashboard reads it (its `/api/summary`), in credits. */
export interface Credits {
	project: { id: string; code: string; name: string };
	/** When the dashboard last pulled iService (ISO), if known. */
	generated_at: string | null;
	/** Credits left, and granted over the wallet's life. */
	balance: number;
	granted: number;
	/** YYYY-MM-DD: when the grant expires. */
	valid_to: string;
	/** One month's spend (Taiwan time), in all and by service. */
	month: { month: string; spent: number; by_service: Record<CreditService, number> };
	/** At the last 7 days' pace: credits a day, and the day they run out. */
	runway: { per_day: number; out: string | null };
	/** The dashboard on this workspace. */
	url: string;
}

/** "9,620,967": credits are whole for reading. */
export const credits = (n: number) => Math.round(n).toLocaleString('en-US');
