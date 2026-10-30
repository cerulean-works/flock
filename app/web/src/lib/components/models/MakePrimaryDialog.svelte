<script lang="ts">
	import { toast } from 'svelte-sonner';
	import * as AlertDialog from '#lib/components/ui/alert-dialog/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { modelApi, modelCall } from '#lib/models.ts';
	import FormError from '../FormError.svelte';

	interface Props {
		open: boolean;
		org: string;
		name: string;
		/** The version to make primary. */
		number: number;
		/** The primary version now. */
		current: number | null;
		onchanged: () => void;
	}

	let { open = $bindable(), org, name, number, current, onchanged }: Props = $props();

	let pending = $state(false);
	let error = $state<string | null>(null);

	async function promote() {
		pending = true;
		error = null;
		try {
			await modelCall(modelApi(org, name, 'primary'), {
				method: 'PUT',
				body: { version: number }
			});
			toast.success(`Version ${number} is primary`);
			open = false;
			onchanged();
		} catch (e) {
			error = (e as Error).message;
		} finally {
			pending = false;
		}
	}
</script>

<AlertDialog.Root bind:open onOpenChange={(o) => o || (error = null)}>
	<AlertDialog.Content class="sm:max-w-md">
		<AlertDialog.Header>
			<AlertDialog.Title>Make version {number} primary?</AlertDialog.Title>
			<AlertDialog.Description>
				{#if current !== null && current !== number}
					Loading {org}/{name} without a version will give version {number} instead of {current}.
				{:else}
					Loading {org}/{name} without a version will give version {number}.
				{/if}
			</AlertDialog.Description>
		</AlertDialog.Header>
		<FormError {error} />
		<AlertDialog.Footer>
			<AlertDialog.Cancel>Cancel</AlertDialog.Cancel>
			<Button disabled={pending} onclick={promote}>
				{pending ? 'Saving…' : 'Make primary'}
			</Button>
		</AlertDialog.Footer>
	</AlertDialog.Content>
</AlertDialog.Root>
