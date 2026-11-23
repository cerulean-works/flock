<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { goto, invalidate } from '$app/navigation';
	import * as AlertDialog from '#lib/components/ui/alert-dialog/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { GROUPS, GroupsError, groupsApi, groupsCall, groupsHref } from '#lib/groups.ts';

	/**
	 * Deleting a group, confirmed: the group goes, its runs stay. Then off to the Groups page
	 * (the group's own page, read again, would find it gone), and the groups are read again.
	 */

	interface Props {
		open: boolean;
		entity: string;
		project: string;
		group: { id: number; name: string };
		/** Before leaving for the Groups page: e.g. closing the dialog the editor is in. */
		ondeleted?: () => void;
	}

	let { open = $bindable(false), entity, project, group, ondeleted }: Props = $props();

	let deleting = $state(false);

	async function remove() {
		deleting = true;
		try {
			await groupsCall(groupsApi(entity, project, String(group.id)), { method: 'DELETE' });
			toast.success(`Deleted ${group.name}`);
			ondeleted?.();
			await goto(groupsHref(entity, project), { replaceState: true });
			await invalidate(GROUPS);
		} catch (err) {
			toast.error(err instanceof GroupsError ? err.message : 'Couldn’t delete the group');
		} finally {
			deleting = false;
			open = false;
		}
	}
</script>

<AlertDialog.Root bind:open>
	<AlertDialog.Content class="sm:max-w-md">
		<AlertDialog.Header>
			<AlertDialog.Title>Delete {group.name}?</AlertDialog.Title>
			<AlertDialog.Description>
				The group goes; its runs stay in the project, and in any other group they’re in.
			</AlertDialog.Description>
		</AlertDialog.Header>
		<AlertDialog.Footer>
			<AlertDialog.Cancel>Cancel</AlertDialog.Cancel>
			<Button variant="destructive" disabled={deleting} onclick={remove}>
				{deleting ? 'Deleting…' : 'Delete group'}
			</Button>
		</AlertDialog.Footer>
	</AlertDialog.Content>
</AlertDialog.Root>
