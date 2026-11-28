import { defineEnvVars } from '@sveltejs/kit/env';

export const variables = defineEnvVars({
	FLOCK_API_URL: {
		description: 'Base URL of the Flock, as this server reaches it.',
		schema: (value) => (value || 'http://127.0.0.1:8080').replace(/\/+$/, '')
	},
	FLOCK_API_PUBLIC_URL: {
		description:
			'Flock as the wandb SDK on your machines reaches it, shown in setup instructions (default: FLOCK_API_URL).',
		schema: (value) => value?.replace(/\/+$/, '') || undefined
	},
	FLOCK_CREDITS_URL: {
		description:
			"The NCHC credit dashboard (cerulean-works/dashboard), as this server reaches it, e.g. on Dokploy's internal network: Home and the org's runs show its wallet in place of Flock's GPU budget. Unset, they show Flock's.",
		schema: (value) => value?.replace(/\/+$/, '') || undefined
	},
	FLOCK_CREDITS_PROJECT: {
		description:
			"The iService workspace id the credit dashboard reads (default: the dashboard's own default).",
		schema: (value) => value || undefined
	},
	FLOCK_CREDITS_TOKEN: {
		description: "The credit dashboard's BURN_API_TOKEN, when it sets one.",
		schema: (value) => value || undefined
	}
});
