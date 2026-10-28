<script lang="ts">
	import { enhance } from '$app/forms';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import * as Select from '#lib/components/ui/select/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import { MAX_LIFETIMES, ephemeralChoices, fits, lifetimeLabel } from '#lib/s3.ts';
	import type { OrgInfo } from '#lib/types.ts';
	import FormError from './FormError.svelte';

	let { org }: { org: OrgInfo } = $props();

	const key = (seconds: number | null) => String(seconds ?? 'never');
	let max = $derived(key(org.s3_key_max_ttl));
	let ephemeral = $derived(String(org.s3_ephemeral_ttl));
	const maxSeconds = $derived(max === 'never' ? null : Number(max));
	const choices = $derived(ephemeralChoices(maxSeconds, org.s3_ephemeral_ttl));
	$effect(() => {
		// Lowering the maximum below the ephemeral lifetime lowers that too.
		if (!fits(Number(ephemeral), maxSeconds)) ephemeral = String(maxSeconds);
	});
	const changed = $derived(
		max !== key(org.s3_key_max_ttl) || ephemeral !== String(org.s3_ephemeral_ttl)
	);
	const mutation = new Mutation({ success: () => 'Key lifetimes saved' });
</script>

<p class="mt-2 max-w-[62ch] text-muted-foreground">
	Keys from <code class="font-mono text-[0.9em]">flock s3 credentials</code> and Python renew themselves,
	so they can be short. Keys made on account pages last longer, up to the maximum. Lowering the maximum
	also cuts off older keys at once.
</p>
<form
	method="POST"
	action="?/s3Lifetimes"
	class="mt-4 grid max-w-120 gap-3"
	use:enhance={mutation.enhance}
>
	<input type="hidden" name="max" value={max} />
	<input type="hidden" name="ephemeral" value={ephemeral} />
	<div class="flex flex-wrap items-end gap-2">
		<div class="grid gap-2">
			<Label for="s3-ephemeral">Ephemeral keys last</Label>
			<Select.Root type="single" bind:value={ephemeral}>
				<Select.Trigger id="s3-ephemeral" class="w-36">
					{lifetimeLabel(Number(ephemeral))}
				</Select.Trigger>
				<Select.Content>
					{#each choices as choice (choice.seconds)}
						<Select.Item value={String(choice.seconds)} label={choice.label}>
							{choice.label}
						</Select.Item>
					{/each}
				</Select.Content>
			</Select.Root>
		</div>
		<div class="grid gap-2">
			<Label for="s3-max">No key lasts more than</Label>
			<Select.Root type="single" bind:value={max}>
				<Select.Trigger id="s3-max" class="w-36">
					{max === 'never' ? 'No limit' : lifetimeLabel(Number(max))}
				</Select.Trigger>
				<Select.Content>
					{#each MAX_LIFETIMES as choice (choice.label)}
						<Select.Item
							value={key(choice.seconds)}
							label={choice.seconds === null ? 'No limit' : choice.label}
						>
							{choice.seconds === null ? 'No limit' : choice.label}
						</Select.Item>
					{/each}
				</Select.Content>
			</Select.Root>
		</div>
		<Button type="submit" variant="outline" disabled={!changed || mutation.pending}>
			{mutation.pending ? 'Saving…' : 'Save'}
		</Button>
	</div>
	<FormError error={mutation.error} />
</form>
