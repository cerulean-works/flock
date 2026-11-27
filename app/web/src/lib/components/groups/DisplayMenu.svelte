<script lang="ts" module>
	/** One of the menu's settings: a choice from a list, as a ToolbarPicker's. */
	export interface DisplaySetting {
		label: string;
		/** The choice; null for `none`. */
		value: string | null;
		options: { value: string | null; label: string }[];
		onchange: (value: string | null) => void;
		/** Choices are column keys: shown in the code font. */
		mono?: boolean;
		/** What the setting does, under its heading's hover. */
		title?: string;
	}
</script>

<script lang="ts">
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import PaletteIcon from '@lucide/svelte/icons/palette';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';

	/**
	 * **Display**: how a group's charts draw its runs (Group by, Color by, Show, Repeats), in one
	 * menu at the toolbar's right end, each setting a section of choices.
	 */

	interface Props {
		settings: DisplaySetting[];
		disabled?: boolean;
	}

	let { settings, disabled = false }: Props = $props();

	/** Radio items take strings: null travels as the empty string. */
	const NONE = '';
	/** Lists longer than this open in a submenu. */
	const INLINE = 6;
	const shown = (s: DisplaySetting) =>
		s.options.find((o) => o.value === s.value)?.label ?? s.value ?? 'none';
	const summary = $derived(settings.map((s) => `${s.label}: ${shown(s)}`).join('\n'));
</script>

{#snippet choices(s: DisplaySetting)}
	<DropdownMenu.RadioGroup
		value={s.value ?? NONE}
		onValueChange={(v) => s.onchange(v === NONE ? null : v)}
	>
		{#each s.options as o (o.value ?? NONE)}
			<DropdownMenu.RadioItem
				value={o.value ?? NONE}
				class={s.mono && o.value !== null ? 'font-mono text-[12.5px]' : ''}
			>
				<span class="truncate">{o.label}</span>
			</DropdownMenu.RadioItem>
		{/each}
	</DropdownMenu.RadioGroup>
{/snippet}

{#if settings.length}
	<DropdownMenu.Root>
		<DropdownMenu.Trigger {disabled}>
			{#snippet child({ props })}
				<Button {...props} variant="quiet" size="bar" title={`Display preferences\n${summary}`}>
					<PaletteIcon />
					Display
					<ChevronDownIcon class="text-muted-foreground" />
				</Button>
			{/snippet}
		</DropdownMenu.Trigger>
		<DropdownMenu.Content align="end" class="w-60">
			{#each settings as s, i (s.label)}
				{#if i}<DropdownMenu.Separator />{/if}
				<DropdownMenu.Group>
					<DropdownMenu.Label title={s.title}>{s.label}</DropdownMenu.Label>
					{#if s.options.length > INLINE}
						<!-- A long list of keys opens to the side, so the sections below stay in view. -->
						<DropdownMenu.Sub>
							<DropdownMenu.SubTrigger>
								<span
									class={[
										'min-w-0 flex-1 truncate',
										s.mono && s.value !== null && 'font-mono text-[12.5px]'
									]}
								>
									{shown(s)}
								</span>
							</DropdownMenu.SubTrigger>
							<!-- Beside the menu (left: it sits at the window's right edge), its first choice
							     level with the row that opened it, and no taller than the window has room
							     for, so it scrolls rather than running off the bottom. -->
							<DropdownMenu.SubContent
								side="left"
								sideOffset={6}
								alignOffset={-4}
								collisionPadding={12}
								class="max-h-[min(20rem,var(--bits-dropdown-menu-content-available-height))] min-w-44 overflow-y-auto"
							>
								{@render choices(s)}
							</DropdownMenu.SubContent>
						</DropdownMenu.Sub>
					{:else}
						{@render choices(s)}
					{/if}
				</DropdownMenu.Group>
			{/each}
		</DropdownMenu.Content>
	</DropdownMenu.Root>
{/if}
