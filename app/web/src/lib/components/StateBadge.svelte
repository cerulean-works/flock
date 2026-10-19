<script lang="ts">
	import { Badge } from '#lib/components/ui/badge/index.js';
	import type { RunState } from '#lib/types.ts';

	let { state }: { state: string } = $props();

	const LABEL: Record<RunState, string> = {
		running: 'Running',
		preempting: 'Preempting',
		finished: 'Finished',
		crashed: 'Crashed',
		failed: 'Failed'
	};

	// Static class strings per state so Tailwind sees every class.
	const TONE: Record<RunState, string> = {
		running: 'bg-state-running/10 text-state-running',
		preempting: 'bg-state-preempting/10 text-state-preempting',
		finished: 'bg-state-finished/10 text-state-finished',
		crashed: 'bg-state-failed/10 text-state-failed',
		failed: 'bg-state-failed/10 text-state-failed'
	};

	const label = $derived(
		LABEL[state as RunState] ?? state.charAt(0).toUpperCase() + state.slice(1)
	);
	const live = $derived(state === 'running' || state === 'preempting');
</script>

<Badge variant="secondary" class={TONE[state as RunState] ?? 'text-muted-foreground'}>
	<span
		class={['size-1.5 rounded-full bg-current', live && 'animate-live motion-reduce:animate-none']}
	></span>
	{label}
</Badge>
