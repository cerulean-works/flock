<script lang="ts">
	import { enhance } from '$app/forms';
	import FormError from '#lib/components/FormError.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import type { Billing } from '#lib/types.ts';
	import { hours } from '#lib/utils.ts';

	/** Admins change the budget and prices; members see them. */
	let { billing, admin }: { billing: Billing; admin: boolean } = $props();

	// Every GPU type the org's runs used, and any priced ahead of use.
	const types = $derived.by(() => {
		// eslint-disable-next-line svelte/prefer-svelte-reactivity -- local to this computation
		const seen = new Map(billing.gpu_types.map((t) => [t.gpu_type.toLowerCase(), t]));
		for (const r of billing.rates)
			if (!seen.has(r.gpu_type.toLowerCase()))
				seen.set(r.gpu_type.toLowerCase(), { ...r, gpu_hours: 0, runs: 0 });
		return [...seen.values()].sort((a, b) => b.gpu_hours - a.gpu_hours);
	});

	let newType = $state('');
	let newRate = $state<number | null>(null);
	const saving = new Mutation({
		success: () => 'Budget saved',
		done: () => {
			newType = '';
			newRate = null;
		}
	});

	const GRID =
		'grid grid-cols-[minmax(0,1fr)_80px_128px] sm:grid-cols-[minmax(0,1fr)_96px_64px_144px] items-center gap-x-4';
</script>

<form method="POST" action="?/billing" class="mt-4 grid gap-6" use:enhance={saving.enhance}>
	<fieldset class="flex flex-wrap items-end gap-3" disabled={!admin}>
		<div class="grid gap-2">
			<Label for="monthly-budget">Monthly budget</Label>
			<Input
				id="monthly-budget"
				name="monthly_budget"
				type="number"
				min="0"
				step="any"
				class="w-44"
				placeholder="No budget"
				value={billing.monthly_budget ?? ''}
			/>
		</div>
		<div class="grid gap-2">
			<Label for="currency">Currency</Label>
			<Input
				id="currency"
				name="currency"
				class="w-20 uppercase"
				maxlength={3}
				autocomplete="off"
				spellcheck="false"
				value={billing.currency}
			/>
		</div>
	</fieldset>

	<div>
		<h3 class="text-[13px] font-medium text-muted-foreground">Price per GPU hour</h3>
		<div class="mt-2 border-t border-divider">
			<div class={[GRID, 'h-9 border-b border-divider text-xs font-medium text-muted-foreground']}>
				<span>GPU</span>
				<span class="text-right">GPU hours</span>
				<span class="hidden text-right sm:block">Runs</span>
				<span>Price</span>
			</div>
			<fieldset disabled={!admin}>
				<ul>
					{#each types as t (t.gpu_type)}
						<li class={[GRID, 'min-h-11 border-b border-divider py-1.5']}>
							<label for="rate-{t.gpu_type}" class="truncate font-medium">{t.gpu_type}</label>
							<span class="text-right text-muted-foreground tabular-nums">{hours(t.gpu_hours)}</span
							>
							<span class="hidden text-right text-muted-foreground tabular-nums sm:block">
								{t.runs.toLocaleString('en-US')}
							</span>
							<Input
								id="rate-{t.gpu_type}"
								name="rate:{t.gpu_type}"
								aria-label="{t.gpu_type} price per GPU hour"
								type="number"
								min="0"
								step="any"
								class="h-8"
								placeholder="No price"
								value={t.hourly_cost ?? ''}
							/>
						</li>
					{:else}
						<li class="border-b border-divider py-3 text-[13px] text-muted-foreground">
							No GPUs seen yet.
						</li>
					{/each}
					{#if admin}
						<li class={[GRID, 'min-h-11 border-b border-divider py-1.5']}>
							<Input
								name="new_type"
								class="h-8"
								placeholder="Another GPU, e.g. H200"
								aria-label="GPU type to price"
								autocomplete="off"
								spellcheck="false"
								maxlength={64}
								bind:value={newType}
							/>
							<span></span>
							<span class="hidden sm:block"></span>
							<Input
								name="new_rate"
								type="number"
								min="0"
								step="any"
								class="h-8"
								placeholder="Price"
								aria-label="Its price per GPU hour"
								bind:value={newRate}
							/>
						</li>
					{/if}
				</ul>
			</fieldset>
		</div>
		<p class="mt-2 text-[13px] text-muted-foreground">
			Budgets reset each UTC month. A price change also re-prices past months.
		</p>
	</div>

	{#if admin}
		<div class="grid gap-2">
			<FormError error={saving.error} />
			<div>
				<Button type="submit" variant="outline" disabled={saving.pending}>
					{saving.pending ? 'Saving…' : 'Save'}
				</Button>
			</div>
		</div>
	{/if}
</form>
