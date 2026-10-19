<script lang="ts">
	import { enhance } from '$app/forms';
	import * as AlertDialog from '#lib/components/ui/alert-dialog/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import FormError from './FormError.svelte';

	interface Props {
		open: boolean;
		title: string;
		description: string;
		/** The page action to post to, with `fields` as the form. */
		action: string;
		fields: Record<string, string | number>;
		confirm: string;
		pending: string;
		/** Toast once it's done. */
		success: string;
	}

	let {
		open = $bindable(),
		title,
		description,
		action,
		fields,
		confirm,
		pending,
		success
	}: Props = $props();

	const mutation = new Mutation({ success: () => success, done: () => (open = false) });
</script>

<!-- A destructive change, confirmed. -->
<AlertDialog.Root bind:open onOpenChange={(o) => o || mutation.reset()}>
	<AlertDialog.Content class="sm:max-w-md">
		<AlertDialog.Header>
			<AlertDialog.Title>{title}</AlertDialog.Title>
			<AlertDialog.Description>{description}</AlertDialog.Description>
		</AlertDialog.Header>
		<form method="POST" {action} class="grid gap-4" use:enhance={mutation.enhance}>
			{#each Object.entries(fields) as [name, value] (name)}
				<input type="hidden" {name} {value} />
			{/each}
			<FormError error={mutation.error} />
			<AlertDialog.Footer>
				<AlertDialog.Cancel>Cancel</AlertDialog.Cancel>
				<Button type="submit" variant="destructive" disabled={mutation.pending}>
					{mutation.pending ? pending : confirm}
				</Button>
			</AlertDialog.Footer>
		</form>
	</AlertDialog.Content>
</AlertDialog.Root>
