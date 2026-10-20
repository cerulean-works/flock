<script lang="ts">
	import { untrack } from 'svelte';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { slugify } from '#lib/utils.ts';

	interface Props {
		/** The display name typed so far (the `display_name` field). */
		displayName?: string;
		/** The ID typed so far, or suggested from the display name (the `id` field). */
		id?: string;
		/** Prefixes the inputs' ids, for pages with more than one form. */
		prefix?: string;
		/** Taller inputs, as on the setup page. */
		large?: boolean;
		invalid?: boolean;
	}

	let {
		displayName = $bindable(''),
		id = $bindable(''),
		prefix = 'org',
		large = false,
		invalid = false
	}: Props = $props();

	// The ID follows the display name until it's typed in by hand (a refused form's kept ID
	// counts, unless it is the suggestion). Clearing it hands it back to the suggestion.
	let edited = $state(untrack(() => !!id && id !== slugify(displayName)));
</script>

<div class="grid gap-2">
	<Label for="{prefix}-display-name">Display name</Label>
	<Input
		id="{prefix}-display-name"
		name="display_name"
		autocomplete="organization"
		placeholder="e.g. Cerulean Research"
		value={displayName}
		oninput={(e) => {
			displayName = e.currentTarget.value;
			if (!edited) id = slugify(displayName);
		}}
		maxlength={128}
		class={[large && 'h-10']}
		aria-invalid={invalid}
		required
	/>
	<p class="text-xs leading-relaxed text-muted-foreground">What people see. You can change it.</p>
</div>
<div class="grid gap-2">
	<Label for="{prefix}-id">ID</Label>
	<Input
		id="{prefix}-id"
		name="id"
		placeholder="e.g. cerulean-research"
		autocomplete="off"
		spellcheck="false"
		value={id}
		oninput={(e) => {
			id = e.currentTarget.value;
			edited = !!id;
		}}
		maxlength={128}
		class={['font-mono text-[13px]', large && 'h-10']}
		aria-invalid={invalid}
		required
	/>
	<p class="text-xs leading-relaxed text-muted-foreground">
		It appears in links, and the wandb SDK logs to it as the <code
			class="rounded bg-muted px-1 py-px font-mono text-[11px]">entity</code
		>. It can’t be changed later.
	</p>
</div>
