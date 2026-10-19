<script lang="ts">
	import EllipsisIcon from '@lucide/svelte/icons/ellipsis';
	import PencilIcon from '@lucide/svelte/icons/pencil';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';
	import type { ProjectInfo } from '#lib/types.ts';
	import DeleteProjectDialog from './DeleteProjectDialog.svelte';
	import ProjectDialog from './ProjectDialog.svelte';

	interface Props {
		entity: string;
		project: ProjectInfo;
		from?: 'project' | 'list';
		/** For the trigger, e.g. to show it only while its row is hovered. */
		class?: string;
	}

	let { entity, project, from = 'list', class: className }: Props = $props();

	let editing = $state(false);
	let deleting = $state(false);
</script>

<DropdownMenu.Root>
	<DropdownMenu.Trigger>
		{#snippet child({ props })}
			<Button
				{...props}
				variant="ghost"
				size="icon-xs"
				class={className}
				aria-label="{project.name} actions"
			>
				<EllipsisIcon />
			</Button>
		{/snippet}
	</DropdownMenu.Trigger>
	<DropdownMenu.Content align="end" class="w-44">
		<DropdownMenu.Item onSelect={() => (editing = true)}>
			<PencilIcon /> Edit project…
		</DropdownMenu.Item>
		<DropdownMenu.Separator />
		<DropdownMenu.Item variant="destructive" onSelect={() => (deleting = true)}>
			<Trash2Icon /> Delete project…
		</DropdownMenu.Item>
	</DropdownMenu.Content>
</DropdownMenu.Root>

<ProjectDialog bind:open={editing} {entity} {project} {from} />
<DeleteProjectDialog bind:open={deleting} {entity} {project} {from} />
