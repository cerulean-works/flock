<script lang="ts" module>
	import type { SubmitFunction } from '$app/forms';

	/**
	 * A setting that saves by itself (G4): on Enter or when its field loses focus, or as soon as
	 * a switch or a select changes. `save` sends the form when something changed; `saved` shows
	 * the tick for a moment after it lands.
	 */
	export class AutoSave {
		shown = $state(false);
		#timer: ReturnType<typeof setTimeout> | undefined;

		/** Sends `form`, from a field's blur or a control's change, when `changed` says so. */
		save(form: HTMLFormElement | null | undefined, changed = true) {
			if (form && changed) form.requestSubmit();
		}

		/** A form's submit, sent only when something changed: Enter on an unchanged field does nothing. */
		only(changed: () => boolean, submit: SubmitFunction): SubmitFunction {
			return (input) => (changed() ? submit(input) : input.cancel());
		}

		/** After a change lands: the tick, for two seconds. */
		saved = () => {
			this.shown = true;
			clearTimeout(this.#timer);
			this.#timer = setTimeout(() => (this.shown = false), 2000);
		};
	}
</script>

<script lang="ts">
	import CheckIcon from '@lucide/svelte/icons/check';
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';

	let {
		saving,
		pending = false,
		overlay = false
	}: {
		saving: AutoSave;
		pending?: boolean;
		/** Inside a text field's right end (its parent `relative`), so the field keeps its width. */
		overlay?: boolean;
	} = $props();
</script>

<!-- Beside the field it saves: a spinner while it's sent, then a quiet tick. -->
<span
	class={[
		'inline-flex h-5 flex-none items-center gap-1 text-[12px] text-muted-foreground',
		overlay ? 'pointer-events-none absolute top-1/2 right-2.5 -translate-y-1/2' : 'min-w-14'
	]}
	role="status"
	aria-live="polite"
>
	{#if pending}
		<LoaderCircleIcon class="size-3.5 animate-spin" aria-label="Saving" />
	{:else if saving.shown}
		<CheckIcon class="size-3.5" /> Saved
	{/if}
</span>
