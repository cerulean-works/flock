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
	}
});
