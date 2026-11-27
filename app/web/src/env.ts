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
	FLOCK_SLURM_URL: {
		description:
			'The Slurm queue dashboard (cerulean-works/slurm-dashboard), as this server reaches it: Home shows its GPU use. Unset, Home leaves it out.',
		schema: (value) => value?.replace(/\/+$/, '') || undefined
	},
	FLOCK_SLURM_PUBLIC_URL: {
		description:
			'The Slurm dashboard as people open it, for Home to link to (default: FLOCK_SLURM_URL).',
		schema: (value) => value?.replace(/\/+$/, '') || undefined
	},
	FLOCK_SLURM_ACCESS_ID: {
		description:
			'A Cloudflare Access service token id for the Slurm dashboard, when it sits behind Access.',
		schema: (value) => value || undefined
	},
	FLOCK_SLURM_ACCESS_SECRET: {
		description: 'The service token secret that goes with FLOCK_SLURM_ACCESS_ID.',
		schema: (value) => value || undefined
	}
});
