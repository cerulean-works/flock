<script lang="ts">
	import Keys from '#lib/components/Keys.svelte';
	import * as Dialog from '#lib/components/ui/dialog/index.js';

	import { page } from '$app/state';

	let { open = $bindable(false) }: { open?: boolean } = $props();

	// Datasets and Models exist only on a server with storage.
	const s3 = $derived(!!page.data.shell?.s3);
	const GROUPS = $derived<[string, [string, string][]][]>([
		[
			'Anywhere',
			[
				['⌘ K', 'Search and run commands'],
				['?', 'These shortcuts'],
				['[', 'Hide or show the sidebar'],
				[']', 'Hide or show the properties'],
				['G then H', 'Go to Home'],
				['G then R', 'Go to Runs'],
				...(s3
					? ([
							['G then D', 'Go to Datasets'],
							['G then M', 'Go to Models'],
							['G then S', 'Go to Storage']
						] as [string, string][])
					: []),
				['G then ,', 'Go to Settings']
			]
		],
		[
			'Lists',
			[
				['/', 'Search'],
				['J  K', 'Next and previous row'],
				['Enter', 'Open the row'],
				['X', 'Select the row'],
				['Esc', 'Let go, or clear the selection']
			]
		],
		[
			'Runs',
			[
				['T  V  C', 'Table, Visualize, Charts'],
				['V', 'On Visualize: stacked or side by side'],
				['↑  ↓', 'Pick a run'],
				['Enter', 'Tick the run'],
				['→  O', 'Open the run'],
				['F', 'Filter runs'],
				['⇧ S', 'Sort runs']
			]
		]
	]);
</script>

<!-- The '?' sheet (Nov 24): every key, in place of the hints that sat under lists. -->
<Dialog.Root bind:open>
	<Dialog.Content class="max-h-[80vh] gap-0 overflow-y-auto p-0 sm:max-w-md">
		<Dialog.Title class="px-5 pt-4 pb-2 text-[15px] font-medium">Keyboard shortcuts</Dialog.Title>
		<div class="grid gap-4 px-5 pb-5">
			{#each GROUPS as [name, keys] (name)}
				<section>
					<h3 class="mb-1 text-xs font-medium text-muted-foreground">{name}</h3>
					<dl class="grid grid-cols-[1fr_auto] gap-y-1 text-[13px]">
						{#each keys as [key, what] (key)}
							<dt>{what}</dt>
							<dd class="flex justify-end"><Keys keys={key} size="md" /></dd>
						{/each}
					</dl>
				</section>
			{/each}
		</div>
	</Dialog.Content>
</Dialog.Root>
