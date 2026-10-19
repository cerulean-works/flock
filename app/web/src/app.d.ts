// See https://svelte.dev/docs/kit/types#app.d.ts
// for information about these interfaces
import type { SessionUser } from '#lib/server/session.ts';

declare global {
	namespace App {
		// interface Error {}
		interface Locals {
			/** What requests to Flock authenticate with: the session's access token. */
			token: string | null;
			/** The signed-in user; null when nobody is signed in. */
			user: SessionUser | null;
		}
		// interface PageData {}
		// interface PageState {}
		// interface Platform {}
	}
}

export {};
