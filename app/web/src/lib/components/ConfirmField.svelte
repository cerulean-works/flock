<script lang="ts">
	import { Input } from '#lib/components/ui/input/index.js';
	import CopyButton from './CopyButton.svelte';

	interface Props {
		/** The input's id. */
		id: string;
		/** What has to be typed (or pasted) to confirm. */
		text: string;
		/** What's in the input. */
		value: string;
		disabled?: boolean;
		/** The words before the text, and after it. */
		before?: string;
		after?: string;
	}

	let {
		id,
		text,
		value = $bindable(),
		disabled = false,
		before = 'Type',
		after = ' to confirm'
	}: Props = $props();

	let input = $state<HTMLInputElement | null>(null);
</script>

<!-- A type-to-confirm prompt (D135). The prompt isn't a Label, whose select-none would keep the
     text from being selected, and the copy button puts focus in the input to paste it. -->
<div class="grid gap-2">
	<div class="flex min-w-0 items-center gap-1">
		<p id="{id}-prompt" class="min-w-0 text-sm leading-snug font-medium">
			{before} <span class="font-mono text-[12.5px] break-all">{text}</span>{after}
		</p>
		<CopyButton {text} class="-my-1 size-6 flex-none" oncopied={() => input?.focus()} />
	</div>
	<Input
		{id}
		bind:ref={input}
		bind:value
		aria-labelledby="{id}-prompt"
		autocomplete="off"
		spellcheck="false"
		{disabled}
	/>
</div>
