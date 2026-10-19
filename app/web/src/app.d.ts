// See https://svelte.dev/docs/kit/types#app.d.ts
// for information about these interfaces
import type { SessionUser } from '#lib/server/session.ts';

declare global {
	namespace App {
		// interface Error {}
		interface Locals {
			/** What requests to Flock authenticate with: the session's access token, else FLOCK_API_KEY. */
			token: string | null;
			/** The signed-in user; null when nobody is (or only FLOCK_API_KEY is used). */
			user: SessionUser | null;
		}
		// interface PageData {}
		// interface PageState {}
		// interface Platform {}
	}
}

export {};
