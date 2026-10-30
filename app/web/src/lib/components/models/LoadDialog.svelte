<script lang="ts">
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { loadSnippet, pullSnippet, versionRef } from '#lib/models.ts';
	import CopyBlock from '../CopyBlock.svelte';

	interface Props {
		open: boolean;
		org: string;
		name: string;
		/** The version shown. */
		number: number;
	}

	let { open = $bindable(), org, name, number }: Props = $props();

	// The primary version's ref has no number, so it follows the primary as it moves.
	const snippets = $derived([
		{ label: 'The primary version', ref: `${org}/${name}` },
		{ label: `Version ${number}`, ref: versionRef(org, name, number) }
	]);
</script>

<Dialog.Root bind:open>
	<Dialog.Content class="sm:max-w-xl">
		<Dialog.Header>
			<Dialog.Title>Load version {number}</Dialog.Title>
		</Dialog.Header>
		<div class="grid gap-4">
			{@render section('In Python', loadSnippet)}
			{@render section('From the shell', pullSnippet)}
		</div>
	</Dialog.Content>
</Dialog.Root>

{#snippet section(title: string, code: (ref: string) => string)}
	<section class="grid gap-2" aria-label={title}>
		<h3 class="text-[13px] font-medium">{title}</h3>
		{#each snippets as s (s.label)}
			<div class="grid gap-1.5">
				<p class="text-xs text-muted-foreground">{s.label}</p>
				<CopyBlock code={code(s.ref)} />
			</div>
		{/each}
	</section>
{/snippet}
