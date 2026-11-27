<script lang="ts">
	import { getWorkspace } from '#lib/workspace/workspace.svelte.ts';

	const ws = getWorkspace();
</script>

<!-- The live stream says nothing while it's connected: only a lost connection is news. -->
{#if ws.connection === 'offline'}
	<span
		class="connection flex items-center gap-1.5 text-[13px] text-muted-foreground"
		role="status"
	>
		<span class="size-1.5 rounded-full bg-state-preempting" aria-hidden="true"></span>
		Reconnecting…
	</span>
{/if}
