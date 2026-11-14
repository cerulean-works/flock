<script lang="ts">
	import BoxIcon from '@lucide/svelte/icons/box';
	import { toast } from 'svelte-sonner';
	import * as AlertDialog from '#lib/components/ui/alert-dialog/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { TRASH_DAYS, modelApi, modelCall } from '#lib/models.ts';
	import { count } from '#lib/utils.ts';
	import ConfirmField from '../ConfirmField.svelte';
	import ItemFailures from '../ItemFailures.svelte';

	interface Props {
		open: boolean;
		org: string;
		/** The models' names, two or more (one is DeleteModelDialog's). */
		names: readonly string[];
		/** Once done: the models deleted leave the list. */
		ondeleted: (names: string[]) => void | Promise<void>;
	}

	let { open = $bindable(), org, names, ondeleted }: Props = $props();

	/** The names listed; the rest are counted. */
	const LISTED = 10;

	// Typing how many there are is the confirmation. Reset once the dialog has closed, so its
	// text stays put while it animates closed.
	const expected = $derived(String(names.length));
	let typed = $state('');
	let failures = $state<{ path: string; error: string | null }[]>([]);
	let pending = $state(false);

	function reset() {
		typed = '';
		failures = [];
	}

	// Models are deleted one request each, in turn; one refused leaves the rest going.
	async function remove() {
		pending = true;
		const done: string[] = [];
		const failed: { path: string; error: string | null }[] = [];
		for (const name of names) {
			try {
				await modelCall(modelApi(org, name), { method: 'DELETE' });
				done.push(name);
			} catch (e) {
				failed.push({ path: name, error: (e as Error).message });
			}
		}
		failures = failed;
		try {
			await ondeleted(done);
		} finally {
			pending = false;
		}
		if (done.length) toast.success(`Deleted ${count(done.length, 'model')}`);
		if (!failed.length) open = false;
	}
</script>

<AlertDialog.Root
	bind:open={() => open, (o) => (open = pending ? true : o)}
	onOpenChangeComplete={(o) => o || reset()}
>
	<AlertDialog.Content class="sm:max-w-md">
		<AlertDialog.Header>
			<AlertDialog.Title>Delete {count(names.length, 'model')}?</AlertDialog.Title>
			<AlertDialog.Description>
				All their versions are deleted. Org admins can restore them for {TRASH_DAYS} days.
			</AlertDialog.Description>
		</AlertDialog.Header>
		<form
			class="grid gap-4"
			onsubmit={(e) => {
				e.preventDefault();
				if (typed === expected && !failures.length) void remove();
			}}
		>
			{#if failures.length}
				<ItemFailures
					{failures}
					summary="{count(failures.length, 'model')} {failures.length === 1
						? 'wasn’t'
						: 'weren’t'} deleted."
				/>
			{:else}
				<ul class="max-h-48 overflow-y-auto border-y border-divider py-1 text-[13px]">
					{#each names.slice(0, LISTED) as name (name)}
						<li class="flex items-center gap-2 py-0.5">
							<BoxIcon class="size-4 flex-none text-muted-foreground" />
							<span class="truncate font-mono text-[12.5px]">{org}/{name}</span>
						</li>
					{/each}
					{#if names.length > LISTED}
						<li class="py-0.5 text-muted-foreground">
							and {(names.length - LISTED).toLocaleString('en-US')} more
						</li>
					{/if}
				</ul>
				<ConfirmField
					id="delete-models-confirm"
					text={expected}
					bind:value={typed}
					disabled={pending}
				/>
			{/if}
			<AlertDialog.Footer>
				{#if failures.length}
					<Button type="button" onclick={() => (open = false)}>Close</Button>
				{:else}
					<AlertDialog.Cancel disabled={pending}>Cancel</AlertDialog.Cancel>
					<Button type="submit" variant="destructive" disabled={typed !== expected || pending}>
						{pending ? 'Deleting…' : `Delete ${count(names.length, 'model')}`}
					</Button>
				{/if}
			</AlertDialog.Footer>
		</form>
	</AlertDialog.Content>
</AlertDialog.Root>
