<script lang="ts">
	interface Props {
		error: string | null;
		/** Runs Flock named as being in the way, shown by `label`. */
		ids?: readonly string[];
		label?: (id: string) => string;
	}

	let { error, ids = [], label = (id) => id }: Props = $props();

	const SHOWN = 5;
</script>

{#if error}
	<p class="text-[13px] text-destructive" role="alert">
		{error.charAt(0).toUpperCase() + error.slice(1)}{#if ids.length}:
			{ids.slice(0, SHOWN).map(label).join(', ')}{#if ids.length > SHOWN}
				and {ids.length - SHOWN} more{/if}{/if}
	</p>
{/if}
