<script lang="ts">
	import PickerField from '#lib/components/PickerField.svelte';
	import PickerList from '#lib/components/PickerList.svelte';
	import { scopeLabel } from '#lib/scope.ts';

	/** Where a move goes (D166): "Workspace", the org's own, or one of its projects. */
	interface Props {
		/** The project chosen; null for global. */
		value: string | null;
		/** The org's projects. */
		projects: readonly string[];
		id?: string;
		disabled?: boolean;
	}

	let { value = $bindable(), projects, id, disabled = false }: Props = $props();

	let open = $state(false);
	let query = $state('');
	let active = $state(0);

	// The workspace, then the projects, narrowed by the search.
	const choices = $derived.by(() => {
		const all: (string | null)[] = [null, ...projects];
		// The scope shown is always offered, even if the list doesn't have it.
		if (value !== null && !projects.includes(value)) all.splice(1, 0, value);
		const q = query.trim().toLowerCase();
		const shown: (string | null)[] = [];
		for (const c of all) {
			if (scopeLabel(c).toLowerCase().includes(q)) shown.push(c);
		}
		return shown;
	});

	function onOpenChange(next: boolean) {
		if (!next) return;
		query = '';
		active = Math.max(0, choices.indexOf(value));
	}

	function onpick(choice: string | null) {
		value = choice;
		open = false;
	}
</script>

<PickerField bind:open {onOpenChange} {id} {disabled}>
	<span class="min-w-0 flex-1 truncate">{scopeLabel(value)}</span>
	{#snippet list()}
		<PickerList
			options={choices}
			key={(c) => c ?? ''}
			current={(c) => c === value}
			noun="projects"
			label="Search projects"
			placeholder="Search projects…"
			bind:query
			bind:active
			{onpick}
		>
			{#snippet row(c: string | null)}
				<span class="min-w-0 flex-1 truncate">{scopeLabel(c)}</span>
			{/snippet}
		</PickerList>
	{/snippet}
</PickerField>
