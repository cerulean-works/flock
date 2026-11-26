<script lang="ts">
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';

	/**
	 * A toolbar setting picked from a list, its choice in the button: `Group by batch_size`.
	 * A menu rather than a select, as the Pager's: its items act on click.
	 */

	interface Props {
		label: string;
		/** The choice; null for `none`. */
		value: string | null;
		options: { value: string | null; label: string }[];
		onchange: (value: string | null) => void;
		disabled?: boolean;
		/** Choices are column keys: shown in the code font. */
		mono?: boolean;
		/** Hover text: what the setting does. */
		title?: string;
	}

	let { label, value, options, onchange, disabled = false, mono = false, title }: Props = $props();

	/** Radio items take strings: null travels as the empty string. */
	const NONE = '';
	const shown = $derived(options.find((o) => o.value === value)?.label ?? value ?? 'none');
</script>

<DropdownMenu.Root>
	<DropdownMenu.Trigger {disabled}>
		{#snippet child({ props })}
			<Button
				{...props}
				variant="outline"
				class="gap-1.5 pr-2 font-normal text-muted-foreground"
				{title}
			>
				{label}
				<span
					class={[
						'max-w-40 truncate font-medium text-foreground',
						mono && 'font-mono text-[12.5px]'
					]}
				>
					{shown}
				</span>
				<ChevronDownIcon class="text-muted-foreground" />
			</Button>
		{/snippet}
	</DropdownMenu.Trigger>
	<DropdownMenu.Content align="start" class="max-h-80 min-w-44 overflow-y-auto">
		<DropdownMenu.Group>
			<DropdownMenu.Label>{label}</DropdownMenu.Label>
			<DropdownMenu.RadioGroup
				value={value ?? NONE}
				onValueChange={(v) => onchange(v === NONE ? null : v)}
			>
				{#each options as o (o.value ?? NONE)}
					<DropdownMenu.RadioItem
						value={o.value ?? NONE}
						class={mono && o.value !== null ? 'font-mono text-[12.5px]' : ''}
						closeOnSelect
					>
						{o.label}
					</DropdownMenu.RadioItem>
				{/each}
			</DropdownMenu.RadioGroup>
		</DropdownMenu.Group>
	</DropdownMenu.Content>
</DropdownMenu.Root>
