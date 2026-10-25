<script lang="ts">
	import { enhance } from '$app/forms';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import * as Select from '#lib/components/ui/select/index.js';
	import { Textarea } from '#lib/components/ui/textarea/index.js';
	import FormError from '#lib/components/FormError.svelte';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import type { DatasetVisibility } from '#lib/types.ts';
	import { formText, projectNameError } from '#lib/utils.ts';
	import { datasetsHref } from '#lib/workspace/url.ts';
	import { VISIBILITY } from './visibility.ts';

	interface Props {
		open: boolean;
		entity: string;
		/** What people see the org called. */
		orgName: string;
	}

	let { open = $bindable(), entity, orgName }: Props = $props();

	const mutation = new Mutation({
		validate: (form) => projectNameError(formText(form, 'name')),
		success: () => 'Dataset created',
		done: () => (open = false)
	});

	let visibility = $derived.by<DatasetVisibility>(() => {
		void open;
		return 'private';
	});
</script>

<Dialog.Root bind:open onOpenChange={(o) => o || mutation.reset()}>
	<Dialog.Content class="sm:max-w-md">
		<Dialog.Header>
			<Dialog.Title>New dataset</Dialog.Title>
			<Dialog.Description>In {orgName}. Its first version is its first write.</Dialog.Description>
		</Dialog.Header>
		<form
			method="POST"
			action="{datasetsHref(entity)}?/create"
			class="grid gap-4"
			use:enhance={mutation.enhance}
		>
			<div class="grid gap-2">
				<Label for="dataset-name">Name</Label>
				<Input
					id="dataset-name"
					name="name"
					autocomplete="off"
					spellcheck="false"
					required
					maxlength={128}
					aria-invalid={!!mutation.error}
				/>
			</div>
			<div class="grid gap-2">
				<Label for="dataset-description">
					Description <span class="font-normal text-muted-foreground">optional</span>
				</Label>
				<Textarea id="dataset-description" name="description" rows={3} />
			</div>
			<div class="grid gap-2">
				<Label for="dataset-visibility">Visibility</Label>
				<input type="hidden" name="visibility" value={visibility} />
				<Select.Root type="single" bind:value={visibility}>
					<Select.Trigger id="dataset-visibility" class="w-full">
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
			</div>
			<FormError error={mutation.error} />
			<Dialog.Footer>
				<Dialog.Close>
					{#snippet child({ props })}
						<Button {...props} variant="outline">Cancel</Button>
					{/snippet}
				</Dialog.Close>
				<Button type="submit" disabled={mutation.pending}>
					{mutation.pending ? 'Creating…' : 'Create dataset'}
				</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
