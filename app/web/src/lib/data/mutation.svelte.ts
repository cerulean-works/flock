import type { SubmitFunction } from '$app/forms';
import { goto } from '$app/navigation';
import type { ResolvedPathname } from '$app/types';
import { toast } from 'svelte-sonner';
import { clearRunPages } from './cache.ts';

interface Options<T> {
	/** The change touches runs, so cached run pages are stale once it lands. */
	runs?: boolean;
	/** Checks the form before it is sent: a message keeps it from being sent. */
	validate?: (form: FormData) => string | null;
	/** Called as the form is sent. */
	submit?: (form: FormData) => void;
	/** Toast once the change has landed and the page shows it; from the action's result. */
	success?: (data: T) => string;
	/** Called after that, e.g. to close the dialog. */
	done?: (data: T) => void;
	/**
	 * Where to go once the change lands, instead of reloading this page: a run's own page after
	 * the run was deleted or moved away has nothing left to load.
	 */
	leave?: (data: T) => ResolvedPathname | null | undefined;
	/** Toast refusals too, for forms with no room to show them (menu items, row buttons). */
	toastErrors?: boolean;
}

/**
 * One form action, submitted with `use:enhance={mutation.enhance}`.
 *
 * Refusals (a conflict, an invalid name, runs the user may not change) stay in the form as
 * `error`, with the runs in the way as `ids`. A change that lands reloads the page's data before
 * `pending` clears, so a dialog closes onto the changed page rather than the stale one.
 */
export class Mutation<T = Record<string, unknown>> {
	pending = $state(false);
	error = $state<string | null>(null);
	ids = $state.raw<string[]>([]);
	#opts: Options<T>;

	constructor(opts: Options<T> = {}) {
		this.#opts = opts;
	}

	reset() {
		this.error = null;
		this.ids = [];
	}

	enhance: SubmitFunction = ({ formData, cancel }) => {
		this.reset();
		const invalid = this.#opts.validate?.(formData);
		if (invalid) {
			this.error = invalid;
			cancel();
			return;
		}
		this.pending = true;
		this.#opts.submit?.(formData);
		return async ({ result, update }) => {
			try {
				if (result.type === 'failure' || result.type === 'error') {
					this.error =
						result.type === 'failure'
							? String(result.data?.error ?? 'Something went wrong')
							: (result.error?.message ?? 'Something went wrong');
					if (result.type === 'failure')
						this.ids = (result.data?.ids as string[] | undefined) ?? [];
					if (this.#opts.toastErrors)
						toast.error(this.error.charAt(0).toUpperCase() + this.error.slice(1));
					return;
				}
				if (this.#opts.runs) clearRunPages();
				const data = (result.type === 'success' ? result.data : {}) as T;
				// Reloads the page's data, or follows the action's redirect. Some forms post to
				// another page's action (the workspace edits its project through `/{entity}`):
				// stay on this page rather than land on that one.
				const to = this.#opts.leave?.(data);
				// eslint-disable-next-line svelte/no-navigation-without-resolve -- resolved by its type
				if (to) await goto(to, { replace: true, refreshAll: true });
				else await update({ reset: false, navigate: false });
				if (this.#opts.success) toast.success(this.#opts.success(data));
				this.#opts.done?.(data);
			} finally {
				this.pending = false;
			}
		};
	};
}
