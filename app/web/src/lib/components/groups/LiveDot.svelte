<script lang="ts">
	/** Whether a group page's live stream is connected (D195), as the workspace's LiveStatus. */
	let { connection }: { connection: 'connecting' | 'live' | 'offline' } = $props();
</script>

<span class="connection flex items-center gap-1.5 text-xs text-muted-foreground" role="status">
	<span
		class={[
			'size-[7px] rounded-full',
			connection === 'live'
				? 'bg-state-finished'
				: connection === 'offline'
					? 'bg-state-preempting'
					: 'bg-faint'
		]}
	></span>
	<span class="max-sm:sr-only">
		{connection === 'live' ? 'Live' : connection === 'offline' ? 'Reconnecting…' : 'Connecting…'}
	</span>
</span>
