<script lang="ts">
	import Page from '#lib/components/Page.svelte';
</script>

<svelte:head>
	<title>Flock</title>
</svelte:head>

<!-- With an org the load sends the user to its runs: this is only the page without one. -->
<Page width="home">
	<div class="mx-auto mt-[12vh] max-w-160 text-[17px] leading-[1.45]">
		<h1 class="mb-4 text-[40px] leading-tight tracking-[-0.02em]">No organization yet</h1>
		<p>Ask an admin to invite you.</p>
	</div>
</Page>
