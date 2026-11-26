<script lang="ts">
	import type { RunState } from '#lib/types.ts';

	/** `dotOnPhone`: just the dot on narrow screens, the word kept for screen readers. */
	let { state, dotOnPhone = false }: { state: string; dotOnPhone?: boolean } = $props();

	const LABEL: Record<RunState, string> = {
		running: 'Running',
		preempting: 'Preempting',
		finished: 'Finished',
		crashed: 'Crashed',
		failed: 'Failed'
	};

	// Static class strings per state so Tailwind sees every class. Finished is the quiet case, so
	// the eye lands on what's running or went wrong.
	const DOT: Record<RunState, string> = {
		running: 'bg-state-running',
		preempting: 'bg-state-preempting',
		finished: 'bg-state-finished',
		crashed: 'bg-state-failed',
		failed: 'bg-state-failed'
	};

	const label = $derived(
		LABEL[state as RunState] ?? state.charAt(0).toUpperCase() + state.slice(1)
	);
	const quiet = $derived(state === 'finished' || !(state in LABEL));
</script>

<!-- A run's state: a still dot in its colour and the word. One mark per run; only the page
     header's live count moves. -->
<span
	class={[
		'inline-flex min-w-0 items-center gap-1.5 text-[13px] whitespace-nowrap',
		quiet ? 'text-muted-foreground' : 'text-foreground'
	]}
	data-state={state}
>
	<span class={['size-1.5 flex-none rounded-full', DOT[state as RunState] ?? 'bg-faint']}></span>
	<span class={dotOnPhone ? 'max-sm:sr-only' : undefined}>{label}</span>
</span>
