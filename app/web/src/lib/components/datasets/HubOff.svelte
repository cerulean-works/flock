<script lang="ts">
	import EmptyState from '#lib/components/EmptyState.svelte';
</script>

<!-- What dataset pages show while the hub isn't configured: its routes answer 503. -->
<EmptyState variant="onboarding" title="The hub isn’t set up on this server">
	{#snippet lead()}
		Datasets are kept in an S3-compatible store, and this Flock hasn’t been given one yet.
	{/snippet}
	<p class="mt-6 max-w-[62ch] text-muted-foreground">
		Whoever runs this server sets <code class="font-mono text-[13px]">FLOCK_HUB_S3_ENDPOINT</code>
		and the other <code class="font-mono text-[13px]">FLOCK_HUB_*</code> settings, then restarts Flock.
		Runs and projects work as before.
	</p>
</EmptyState>
