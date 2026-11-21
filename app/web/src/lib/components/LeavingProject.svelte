<script lang="ts">
	interface Props {
		org: string;
		/** Where each item lands in the org's global scope. */
		paths: readonly string[];
		/** The folder they land in. */
		folder: string;
	}

	let { org, paths, folder }: Props = $props();
</script>

<!-- A move out of a project into global (D166, D169): the org sees it from then on. -->
<p class="bg-card px-3 py-2.5 text-[13px]" role="status">
	{#if paths.length === 1}
		It will be visible to the whole org at {org}/{paths[0]}.
	{:else}
		They will be visible to the whole org in {org}{folder ? `/${folder}` : ''}.
	{/if}
</p>
