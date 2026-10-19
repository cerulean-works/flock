<script lang="ts">
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { Switch } from '#lib/components/ui/switch/index.js';
	import { WANDB_FIELDS } from '#lib/wandb.ts';

	interface Props {
		/** The organization's name: the entity imported when none is entered. */
		org?: string;
		/** Whether to import; the fields are only sent while it's on. */
		on?: boolean;
		/** An entity and W&B server to start from, such as those an earlier import used. */
		entity?: string;
		url?: string;
		/** Prefixes the inputs' ids, for pages with more than one of these. */
		id?: string;
		/** Taller inputs, as on the setup page. */
		large?: boolean;
		/** Leaves out the switch: the form is always an import. */
		always?: boolean;
	}

	let {
		org = '',
		on = $bindable(false),
		entity = '',
		url = '',
		id = 'wandb',
		large = false,
		always = false
	}: Props = $props();

	const shown = $derived(always || on);
</script>

<div class={['grid gap-4', !always && 'rounded-lg border border-divider p-3.5']}>
	{#if !always}
		<div class="flex items-start justify-between gap-4">
			<div class="grid gap-1">
				<Label for="{id}-on">Import runs from W&B</Label>
				<p class="text-xs leading-relaxed text-muted-foreground">
					Copy every project and run of a W&B team or user into it. It runs in the background.
				</p>
			</div>
			<Switch id="{id}-on" bind:checked={on} class="mt-0.5" />
		</div>
	{/if}

	{#if shown}
		<input type="hidden" name={WANDB_FIELDS.on} value="on" />
		<div class="grid gap-2">
			<Label for="{id}-entity">W&B entity</Label>
			<Input
				id="{id}-entity"
				name={WANDB_FIELDS.entity}
				value={entity}
				placeholder={org || 'e.g. cerulean-ml'}
				autocomplete="off"
				spellcheck="false"
				maxlength={128}
				class={[large && 'h-10']}
			/>
			<p class="text-xs leading-relaxed text-muted-foreground">
				The team or user in <code class="font-mono text-[11px]">wandb.ai/&lt;entity&gt;</code>.
				{#if org}Leave blank for “{org}”.{/if}
			</p>
		</div>
		<div class="grid gap-2">
			<Label for="{id}-key">W&B API key</Label>
			<Input
				id="{id}-key"
				name={WANDB_FIELDS.key}
				type="password"
				autocomplete="off"
				spellcheck="false"
				required
				class={[large && 'h-10']}
			/>
			<p class="text-xs leading-relaxed text-muted-foreground">
				From <a
					class="text-link hover:underline"
					href="https://wandb.ai/authorize"
					target="_blank"
					rel="noreferrer">wandb.ai/authorize</a
				>. Flock uses it for this import only, and doesn’t keep it.
			</p>
		</div>
		<div class="grid gap-2">
			<Label for="{id}-url">W&B server <span class="font-normal text-faint">(optional)</span></Label
			>
			<Input
				id="{id}-url"
				name={WANDB_FIELDS.url}
				value={url}
				type="url"
				placeholder="https://api.wandb.ai"
				autocomplete="off"
				spellcheck="false"
				class={[large && 'h-10']}
			/>
			<p class="text-xs leading-relaxed text-muted-foreground">
				Only for W&B Dedicated Cloud or a self-managed server.
			</p>
		</div>
	{/if}
</div>
