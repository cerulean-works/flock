import { defineEnvVars } from '@sveltejs/kit/env';

export const variables = defineEnvVars({
	FLOCK_URL: {
		description: 'Base URL of the Flock, as this server reaches it.',
		schema: (value) => (value || 'http://127.0.0.1:8080').replace(/\/+$/, '')
	},
	FLOCK_PUBLIC_URL: {
		description:
			'Flock as the wandb SDK on your machines reaches it, shown in setup instructions (default: FLOCK_URL).',
		schema: (value) => value?.replace(/\/+$/, '') || undefined
	},
	FLOCK_API_KEY: {
		description:
			'An API key to use when nobody is signed in: the dashboard then needs no sign-in and acts as that key’s user (for kiosks and benchmarks).',
		schema: (value) => value || undefined
	}
});
