<script lang="ts">
	import { enhance } from '$app/forms';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import * as Select from '#lib/components/ui/select/index.js';
	import { Switch } from '#lib/components/ui/switch/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import type { AssistantTier, OrgInfo } from '#lib/types.ts';
	import FormError from '../FormError.svelte';

	/**
	 * Admins only: the run groups assistant (D224) for the org, the model that proposes groups
	 * on the Auto tab and its OpenAI service tier (D230).
	 */
	let { org }: { org: OrgInfo } = $props();

	const TIERS: { value: AssistantTier; label: string; hint: string }[] = [
		{ value: 'standard', label: 'Standard', hint: 'OpenAI’s standard speed and price' },
		{ value: 'fast', label: 'Fast', hint: 'Faster, at a premium per token' },
		{ value: 'ultrafast', label: 'Ultrafast', hint: 'Fastest, for the models that offer it' }
	];

	/** Without an OpenAI key on the server, nothing here does anything: the form is disabled. */
	const noKey = $derived(org.assistant_available === false);
	const savedOn = $derived(org.assistant_enabled ?? false);
	const savedModel = $derived(org.assistant_model ?? '');
	const savedTier = $derived<AssistantTier>(org.assistant_tier ?? 'standard');
	let on = $derived(savedOn);
	let model = $derived(savedModel);
	let tier = $derived<string>(savedTier);
	const changed = $derived(on !== savedOn || model.trim() !== savedModel || tier !== savedTier);
	const tierLabel = $derived(TIERS.find((t) => t.value === tier)?.label ?? tier);
	const mutation = new Mutation({
		success: () => (on ? 'Assistant settings saved' : 'Assistant turned off')
	});
</script>

<p class="mt-2 text-muted-foreground">
	The assistant proposes groups on the Auto tab of New group, picks filters in Manual, and suggests
	a group’s columns. It sends requests, column names, values and run names to OpenAI.
</p>
{#if org.assistant_available === false}
	<p class="mt-2 text-[14px] text-muted-foreground" role="status">
		This server has no OpenAI API key (<code>FLOCK_OPENAI_API_KEY</code>), so the assistant stays
		off until one is set.
	</p>
{/if}
<form method="POST" action="?/assistant" class="mt-4 grid gap-4" use:enhance={mutation.enhance}>
	<input type="hidden" name="enabled" value={on ? 'on' : ''} />
	<input type="hidden" name="tier" value={tier} />
	<div class="flex items-center gap-3">
		<Switch id="assistant-enabled" bind:checked={on} disabled={noKey} />
		<label for="assistant-enabled" class="text-[14px]">Turn on the assistant</label>
	</div>
	<div class="flex flex-wrap items-end gap-2">
		<div class="grid gap-2">
			<Label for="assistant-model">Proposal model</Label>
			<Input
				id="assistant-model"
				name="model"
				class="w-60 font-mono text-[13px]"
				placeholder={org.assistant_default_model ?? 'gpt-6.2-sol'}
				autocomplete="off"
				spellcheck={false}
				maxlength={128}
				disabled={noKey}
				bind:value={model}
			/>
		</div>
		<div class="grid gap-2">
			<Label for="assistant-tier">Speed</Label>
			<Select.Root type="single" bind:value={tier} disabled={noKey}>
				<Select.Trigger id="assistant-tier" class="w-36">{tierLabel}</Select.Trigger>
				<Select.Content>
					{#each TIERS as t (t.value)}
						<Select.Item value={t.value} label={t.label}>
							<span class="grid">
								<span>{t.label}</span>
								<span class="text-xs text-muted-foreground">{t.hint}</span>
							</span>
						</Select.Item>
					{/each}
				</Select.Content>
			</Select.Root>
		</div>
		<Button
			type="submit"
			variant="outline"
			disabled={noKey || !changed || mutation.pending}
			pending={mutation.pending}
		>
			Save
		</Button>
	</div>
	<p class="-mt-2 text-xs text-muted-foreground">
		Leave the model empty to use the server’s default, {org.assistant_default_model ??
			'gpt-6.2-sol'}. Fast and Ultrafast cost more per token, and Ultrafast runs on fewer models.
	</p>
	<FormError error={mutation.error} />
</form>
