<script lang="ts">
	import { getWorkspace } from '#lib/workspace/workspace.svelte.ts';

	const ws = getWorkspace();
</script>

<!-- Whether the workspace's live stream is connected. -->
<span class="connection flex items-center gap-1.5 text-xs text-muted-foreground" role="status">
	<span
		class={[
			'size-[7px] rounded-full',
			ws.connection === 'live'
				? 'bg-state-finished'
				: ws.connection === 'offline'
					? 'bg-state-preempting'
					: 'bg-faint'
		]}
	></span>
	{ws.connection === 'live'
		? 'Live'
		: ws.connection === 'offline'
			? 'Reconnecting…'
			: 'Connecting…'}
</span>
