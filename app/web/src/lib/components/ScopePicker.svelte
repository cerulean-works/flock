<script lang="ts">
	import * as Select from '#lib/components/ui/select/index.js';
	import { scopeLabel } from '#lib/scope.ts';

	interface Props {
		/** The project chosen; null for global. */
		value: string | null;
		/** The org's projects. */
		projects: readonly string[];
		id?: string;
		disabled?: boolean;
	}

	let { value = $bindable(), projects, id, disabled = false }: Props = $props();

	/** The Select value that stands for global (project names are never empty). */
	const GLOBAL = '';
	// The scope shown is always offered, even if the list doesn't have it.
	const choices = $derived(
		value === null || projects.includes(value) ? projects : [value, ...projects]
	);
</script>

<!-- Where a move goes (D166): "Global", the org's own, or one of its projects. -->
<Select.Root
	type="single"
	value={value ?? GLOBAL}
	onValueChange={(v) => (value = v === GLOBAL ? null : v)}
	{disabled}
>
	<Select.Trigger {id} class="w-full">{scopeLabel(value)}</Select.Trigger>
	<Select.Content>
		<Select.Item value={GLOBAL} label="Global">Global</Select.Item>
		{#if choices.length}<Select.Separator />{/if}
		{#each choices as name (name)}
			<Select.Item value={name} label={name}>{name}</Select.Item>
		{/each}
	</Select.Content>
</Select.Root>
