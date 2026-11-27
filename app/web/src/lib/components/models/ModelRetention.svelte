<script lang="ts">
	import { tick } from 'svelte';
	import { enhance } from '$app/forms';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Switch } from '#lib/components/ui/switch/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import { TRASH_DAYS } from '#lib/models.ts';
	import type { OrgInfo } from '#lib/types.ts';
	import AnchoredForm from '../AnchoredForm.svelte';
	import FormError from '../FormError.svelte';
	import SavedTick, { AutoSave } from '../SavedTick.svelte';

	/** Admins only: the org's model retention (D109), off by default. */
	let { org }: { org: OrgInfo } = $props();

	const saved = $derived(org.model_retention ?? null);
	const on = $derived(saved !== null);
	let keep = $derived(String(saved ?? 10));
	// Turning it off is sent at once (G4); `off` holds the switch there until it lands.
	let off = $state(false);
	const changed = $derived(off || (on && keep !== String(saved)));
	const saving = new AutoSave();
	const mutation = new Mutation({
		done: () => {
			off = false;
			saving.saved();
		}
	});
	let form = $state<HTMLFormElement>();
	const save = () => void tick().then(() => saving.save(form, changed));

	// Turning it on can start deleting versions, so it asks how many to keep first (Harry).
	let confirming = $state(false);
	let switchEl = $state<HTMLElement | null>(null);
	let first = $state('10');
	const turningOn = new Mutation({
		validate: (form) => {
			const n = Number(form.get('keep'));
			return Number.isInteger(n) && n >= 1 ? null : 'Enter a whole number of versions, from 1 up';
		},
		success: () => 'Retention turned on',
		done: () => (confirming = false)
	});

	// One wording, inline and in the confirm form; a plain number field, without spinners.
	const WORDS = 'untagged versions of each model';
	const NUMBER =
		'h-8 w-20 tabular-nums [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none';

	function flip(next: boolean) {
		if (next) {
			first = keep;
			confirming = true;
		} else if (on) {
			off = true;
			save();
		}
	}
</script>

<p class="mt-2 text-muted-foreground">
	When this is on, Flock deletes older versions that aren’t primary, have no tag other than a
	checkpoint tag and have no eval runs. They wait in Deleted models for {TRASH_DAYS} days.
</p>
<form
	method="POST"
	action="?/modelRetention"
	class="mt-4 grid gap-3"
	bind:this={form}
	use:enhance={saving.only(() => changed, mutation.enhance)}
>
	<input type="hidden" name="keep" value={off ? '' : keep} />
	<div class="flex items-center gap-3">
		<Switch
			id="model-retention"
			bind:ref={switchEl}
			bind:checked={() => on && !off, flip}
			aria-label="Model retention"
		/>
		<label for="model-retention" class="text-[14px]">{on && !off ? 'On' : 'Off'}</label>
		{#if !on || off}<SavedTick {saving} pending={mutation.pending} />{/if}
	</div>
	<!-- The number is asked once: in the confirm form while off, here once it's on. -->
	{#if on && !off}
		<div class="flex flex-wrap items-center gap-x-2 gap-y-2 text-[14px]">
			<label for="model-retention-keep">Keep the newest</label>
			<Input
				id="model-retention-keep"
				class={NUMBER}
				type="number"
				min="1"
				step="1"
				inputmode="numeric"
				aria-label="Untagged versions to keep"
				bind:value={keep}
				onblur={save}
				onkeydown={(e) => {
					if (e.key === 'Escape') {
						keep = String(saved);
						e.currentTarget.blur();
					}
				}}
			/>
			<span>{WORDS}</span>
			<SavedTick {saving} pending={mutation.pending} />
		</div>
	{/if}
	<FormError error={mutation.error} />
</form>

<AnchoredForm
	bind:open={confirming}
	anchor={switchEl}
	title="Turn on model retention"
	submit="Turn on"
	action="?/modelRetention"
	enhance={turningOn.enhance}
	pending={turningOn.pending}
	error={turningOn.error}
	align="start"
	onclosed={() => turningOn.reset()}
>
	<label class="flex flex-wrap items-center gap-2 text-[13px]">
		Keep the newest
		<Input
			name="keep"
			class={NUMBER}
			type="number"
			min="1"
			step="1"
			inputmode="numeric"
			aria-label="Untagged versions to keep"
			bind:value={first}
		/>
		{WORDS}
	</label>
</AnchoredForm>
