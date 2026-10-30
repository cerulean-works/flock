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

	const ref = $derived(versionRef(org, name, number));
</script>

<Dialog.Root bind:open>
	<Dialog.Content class="sm:max-w-xl">
		<Dialog.Header>
			<Dialog.Title>Load version {number}</Dialog.Title>
			<Dialog.Description>
				Leave out @{number} to always load the primary version.
			</Dialog.Description>
		</Dialog.Header>
		<div class="grid gap-4">
			<div class="grid gap-2">
				<h3 class="text-[13px] font-medium">In Python</h3>
				<CopyBlock code={loadSnippet(ref)} />
			</div>
			<div class="grid gap-2">
				<h3 class="text-[13px] font-medium">From the shell</h3>
				<CopyBlock code={pullSnippet(ref)} />
			</div>
		</div>
	</Dialog.Content>
</Dialog.Root>
