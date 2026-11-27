<script lang="ts" module>
	/** One value a column can be filtered to, with how many rows have it. */
	export interface FilterValue {
		value: string;
		label: string;
		count?: number;
		/** A person's face before the name (owners). */
		person?: { name: string; avatar_url: string | null };
		/** A state's dot colour class, as the State column draws it. */
		dot?: string;
	}
</script>

<script lang="ts">
	import FunnelIcon from '@lucide/svelte/icons/funnel';
	import type { ComponentProps } from 'svelte';
	import { hint } from '#lib/hint.svelte.ts';
	import * as Popover from '#lib/components/ui/popover/index.js';
	import ColumnFilterPanel from './ColumnFilterPanel.svelte';

	/**
	 * `values`: a checkbox list, several at once. `range`: at least / at most (numbers, or dates
	 * with `date`). `text`: contains. Changes apply when the popover closes.
	 */
	type Props = Omit<ComponentProps<typeof ColumnFilterPanel>, 'onclose' | 'onback'>;

	let props: Props = $props();
	let open = $state(false);
</script>

<!--
	A column's filter, as the slurm dashboard draws it: a funnel beside the header's label that
	shows on hover and stays, filled, while the column filters the list; its choices in a popover.
	The header must be a `group/h`.
-->
<Popover.Root bind:open>
	<Popover.Trigger>
		{#snippet child({ props: trigger })}
			<button
				{...trigger}
				type="button"
				class={[
					'relative z-2 grid size-5 flex-none place-items-center rounded outline-none hover:bg-muted hover:text-foreground focus-visible:opacity-100 focus-visible:ring-3 focus-visible:ring-ring/50 data-[state=open]:bg-muted data-[state=open]:opacity-100',
					props.active
						? 'text-primary'
						: 'opacity-0 group-hover/h:opacity-100 pointer-coarse:opacity-100'
				]}
				aria-label="Filter {props.label.toLowerCase()}"
				{@attach hint(`Filter ${props.label.toLowerCase()}`)}
				onpointerdown={(e) => e.stopPropagation()}
			>
				<FunnelIcon class={['size-3', props.active && 'fill-current']} />
			</button>
		{/snippet}
	</Popover.Trigger>
	<Popover.Content align="start" class="w-64 gap-0 p-0 text-[13px] font-normal text-foreground">
		<ColumnFilterPanel {...props} onclose={() => (open = false)} />
	</Popover.Content>
</Popover.Root>
