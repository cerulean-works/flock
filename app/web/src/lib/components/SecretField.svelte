<script lang="ts">
	import CopyButton from './CopyButton.svelte';

	interface Props {
		/** What the value is, e.g. "Access key ID". */
		label: string;
		value: string;
		/** Offer a copy button: for secrets, which are shown once. */
		copy?: boolean;
	}

	let { label, value, copy = false }: Props = $props();

	const id = $props.id();
	// "Copy access key ID": the label as it reads mid-sentence.
	const copyLabel = $derived(`Copy ${label.charAt(0).toLowerCase()}${label.slice(1)}`);
</script>

<!-- A read-only value in monospace, with a copy button when it is a secret. min-w-0: a long
     value scrolls rather than widening the column. -->
<div class="grid min-w-0 gap-1.5">
	<label for={id} class="text-[13px] text-muted-foreground">{label}</label>
	<div class="relative min-w-0">
		<input
			{id}
			{value}
			readonly
			spellcheck="false"
			autocomplete="off"
			class={[
				'h-9 w-full min-w-0 rounded-lg bg-muted pl-3 font-mono text-[13px] outline-none focus-visible:ring-3 focus-visible:ring-ring/50',
				copy ? 'pr-11' : 'pr-3'
			]}
			onfocus={(e) => e.currentTarget.select()}
		/>
		{#if copy}
			<CopyButton text={value} label={copyLabel} class="absolute top-1 right-1" />
		{/if}
	</div>
</div>
