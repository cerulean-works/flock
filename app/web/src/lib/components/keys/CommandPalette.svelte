<script lang="ts">
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import CommandMenu from './CommandMenu.svelte';

	let { open = $bindable(false) }: { open?: boolean } = $props();
	let menu = $state<ReturnType<typeof CommandMenu> | null>(null);
</script>

<!-- Cmd+K: the command menu in a dialog. Escape clears the search, then goes up a level, then
     closes. -->
<Dialog.Root bind:open>
	<Dialog.Content
		class="top-[18vh] translate-y-0 gap-0 overflow-hidden p-0 sm:max-w-xl"
		showCloseButton={false}
		onEscapeKeydown={(e) => {
			if (menu?.escape()) e.preventDefault();
		}}
	>
		<Dialog.Title class="sr-only">Command menu</Dialog.Title>
		<CommandMenu bind:this={menu} bind:open />
	</Dialog.Content>
</Dialog.Root>
