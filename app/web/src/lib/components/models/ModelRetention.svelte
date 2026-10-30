<script lang="ts">
	import { enhance } from '$app/forms';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Switch } from '#lib/components/ui/switch/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import { TRASH_DAYS } from '#lib/models.ts';
	import type { OrgInfo } from '#lib/types.ts';
	import FormError from '../FormError.svelte';

	/** Admins only: the org's model retention (D109), off by default. */
	let { org }: { org: OrgInfo } = $props();

	const saved = $derived(org.model_retention ?? null);
	let on = $derived(saved !== null);
	let keep = $derived(String(saved ?? 10));
	const changed = $derived(on !== (saved !== null) || (on && keep !== String(saved)));
	const mutation = new Mutation({
		success: () => (on ? 'Retention saved' : 'Retention turned off')
	});
</script>

<p class="mt-2 max-w-[62ch] text-muted-foreground">
	When this is on, Flock deletes older versions that aren’t primary, have no tag other than a
	checkpoint tag and have no eval runs. They wait in Deleted models for {TRASH_DAYS} days.
</p>
<form
	method="POST"
	action="?/modelRetention"
	class="mt-4 grid max-w-160 gap-3"
	use:enhance={mutation.enhance}
>
	<input type="hidden" name="keep" value={on ? keep : ''} />
	<div class="flex flex-wrap items-center gap-x-3 gap-y-2">
		<Switch id="model-retention" bind:checked={on} aria-label="Retention" />
		<label for="model-retention-keep" class={['text-[14px]', !on && 'text-muted-foreground']}>
			Keep the newest
		</label>
		<Input
			id="model-retention-keep"
			class="w-20 tabular-nums"
			type="number"
			min="1"
			step="1"
			inputmode="numeric"
			aria-label="Untagged versions to keep"
			bind:value={keep}
			disabled={!on}
		/>
		<span class={['text-[14px]', !on && 'text-muted-foreground']}>
			untagged versions of each model
		</span>
		<Button type="submit" variant="outline" disabled={!changed || mutation.pending}>
			{mutation.pending ? 'Saving…' : 'Save'}
		</Button>
	</div>
	<FormError error={mutation.error} />
</form>
