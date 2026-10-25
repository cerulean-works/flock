<script lang="ts">
	import { enhance } from '$app/forms';
	import FormError from '#lib/components/FormError.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import * as Select from '#lib/components/ui/select/index.js';
	import { Textarea } from '#lib/components/ui/textarea/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import type { DatasetInfo, DatasetVisibility } from '#lib/types.ts';
	import { formText, projectNameError } from '#lib/utils.ts';
	import DeleteDatasetDialog from './DeleteDatasetDialog.svelte';
	import { VISIBILITY } from './visibility.ts';

	let { dataset }: { dataset: DatasetInfo } = $props();

	// Members describe the dataset; renaming, visibility and deleting are for admins (D44).
	const admin = $derived(dataset.role === 'admin');

	const describing = new Mutation({ success: () => 'Description saved' });
	const renaming = new Mutation({
		validate: (form) => projectNameError(formText(form, 'name')),
		success: () => 'Dataset renamed'
	});
	const showing = new Mutation({ success: () => 'Visibility changed' });

	let visibility = $derived<DatasetVisibility>(dataset.visibility);
	let deleting = $state(false);

	const SECTION = 'mt-10 first:mt-0';
	const H2 = 'font-serif text-2xl tracking-[-0.01em]';
	const LEAD = 'mt-2 max-w-[62ch] text-muted-foreground';
</script>

<div class="mx-auto max-w-180 px-5 pt-8 pb-16">
	<section class={SECTION}>
		<h2 class={H2}>Description</h2>
		<p class={LEAD}>One line under its name, in lists and on this page.</p>
		<form
			method="POST"
			action="?/describe"
			class="mt-4 grid max-w-140 gap-3"
			use:enhance={describing.enhance}
		>
			<Textarea name="description" value={dataset.description} rows={3} aria-label="Description" />
			<FormError error={describing.error} />
			<div>
				<Button type="submit" variant="outline" disabled={describing.pending}>
					{describing.pending ? 'Saving…' : 'Save'}
				</Button>
			</div>
		</form>
	</section>

	{#if admin}
		<section class={SECTION}>
			<h2 class={H2}>Name</h2>
			<p class={LEAD}>
				Scripts load it as <code class="font-mono text-[13px]">{dataset.org}/{dataset.name}</code>:
				they need the new name once it changes.
			</p>
			<form
				method="POST"
				action="?/rename"
				class="mt-4 grid max-w-100 gap-3"
				use:enhance={renaming.enhance}
			>
				<div class="flex gap-2">
					<Label for="dataset-rename" class="sr-only">Name</Label>
					<Input
						id="dataset-rename"
						name="name"
						value={dataset.name}
						autocomplete="off"
						spellcheck="false"
						maxlength={128}
						required
					/>
					<Button type="submit" variant="outline" disabled={renaming.pending}>
						{renaming.pending ? 'Renaming…' : 'Rename'}
					</Button>
				</div>
				<FormError error={renaming.error} />
			</form>
		</section>

		<section class={SECTION}>
			<h2 class={H2}>Visibility</h2>
			<p class={LEAD}>{VISIBILITY[dataset.visibility].hint} can read it now.</p>
			<form
				method="POST"
				action="?/visibility"
				class="mt-4 grid max-w-100 gap-3"
				use:enhance={showing.enhance}
			>
				<input type="hidden" name="visibility" value={visibility} />
				<div class="flex gap-2">
					<Select.Root type="single" bind:value={visibility}>
						<Select.Trigger class="w-full" aria-label="Visibility">
							{VISIBILITY[visibility].label}
						</Select.Trigger>
						<Select.Content>
							{#each Object.entries(VISIBILITY) as [value, v] (value)}
								<Select.Item {value} label={v.label}>
									<span class="grid gap-0.5">
										<span>{v.label}</span>
										<span class="text-xs text-muted-foreground">{v.hint}</span>
									</span>
								</Select.Item>
							{/each}
						</Select.Content>
					</Select.Root>
					<Button
						type="submit"
						variant="outline"
						disabled={showing.pending || visibility === dataset.visibility}
					>
						{showing.pending ? 'Saving…' : 'Save'}
					</Button>
				</div>
				<FormError error={showing.error} />
			</form>
		</section>

		<section class={SECTION}>
			<h2 class={H2}>Delete</h2>
			<p class={LEAD}>Deletes every version and its files. There is no undo.</p>
			<div class="mt-4">
				<Button variant="destructive" onclick={() => (deleting = true)}>Delete dataset…</Button>
			</div>
		</section>
	{:else}
		<p class="mt-10 text-[13px] text-muted-foreground">
			Admins of {dataset.org} rename it, change who can see it, and delete it.
		</p>
	{/if}
</div>

{#if admin}<DeleteDatasetDialog bind:open={deleting} {dataset} />{/if}
