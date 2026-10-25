<script lang="ts">
	import KeyRoundIcon from '@lucide/svelte/icons/key-round';
	import { enhance } from '$app/forms';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Checkbox } from '#lib/components/ui/checkbox/index.js';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import * as Select from '#lib/components/ui/select/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import { awsConfigureSnippet } from '#lib/datasets.ts';
	import type { CreatedHubKey } from '#lib/types.ts';
	import { formText } from '#lib/utils.ts';
	import CopyBlock from './CopyBlock.svelte';
	import FormError from './FormError.svelte';

	interface Props {
		/** The organizations a key can be limited to. */
		orgs: readonly { name: string; display_name: string }[];
		open?: boolean;
		/** The key just created, shown once: Flock never shows its secret again. */
		created?: CreatedHubKey | null;
	}

	let { orgs, open = $bindable(false), created = $bindable(null) }: Props = $props();

	// No org ID contains a slash; and an empty value would select nothing.
	const ANY = '/';
	let org = $state(ANY);
	let readOnly = $state(false);

	const mutation = new Mutation<{ created: CreatedHubKey }>({
		validate: (form) => (formText(form, 'name') ? null : 'Name the key'),
		success: ({ created }) => `Created S3 key “${created.name}”`,
		done: (data) => (created = data.created)
	});

	function onOpenChange(o: boolean) {
		if (o) return;
		// Closing forgets the secret: it was shown once.
		mutation.reset();
		created = null;
		org = ANY;
		readOnly = false;
	}

	const orgLabel = (name: string) =>
		name === ANY ? 'Every organization' : (orgs.find((o) => o.name === name)?.display_name ?? name);
</script>

<Button variant="outline" onclick={() => (open = true)}>
	<KeyRoundIcon /> Create S3 key
</Button>

<Dialog.Root bind:open {onOpenChange}>
	<Dialog.Content class="sm:max-w-lg">
		{#if created}
			<Dialog.Header>
				<Dialog.Title>S3 key “{created.name}”</Dialog.Title>
				<Dialog.Description>
					Copy the secret now: it isn’t shown again. Deleting the key revokes it.
				</Dialog.Description>
			</Dialog.Header>
			<dl class="grid grid-cols-[max-content_minmax(0,1fr)] items-center gap-x-4 gap-y-2">
				<dt class="text-[13px] text-muted-foreground">Access key ID</dt>
				<dd><CopyBlock code={created.access_key_id} /></dd>
				<dt class="text-[13px] text-muted-foreground">Secret</dt>
				<dd><CopyBlock code={created.secret_access_key} /></dd>
				{#if created.endpoint}
					<dt class="text-[13px] text-muted-foreground">Endpoint</dt>
					<dd class="truncate font-mono text-[13px]">{created.endpoint}</dd>
				{/if}
			</dl>
			<div class="grid gap-2">
				<p class="text-[13px] text-muted-foreground">
					For the AWS CLI, as the profile <code class="font-mono text-[12.5px]">flock</code>:
				</p>
				<CopyBlock code={awsConfigureSnippet(created)} />
			</div>
			<Dialog.Footer>
				<Dialog.Close>
					{#snippet child({ props })}
						<Button {...props}>Done</Button>
					{/snippet}
				</Dialog.Close>
			</Dialog.Footer>
		{:else}
			<Dialog.Header>
				<Dialog.Title>New S3 key</Dialog.Title>
				<Dialog.Description>
					For tools that speak S3: Lance, DuckDB, boto3, the AWS CLI. It reaches the datasets you
					can, with your role.
				</Dialog.Description>
			</Dialog.Header>
			<form
				method="POST"
				action="/account?/createHubKey"
				class="grid gap-4"
				use:enhance={mutation.enhance}
			>
				<div class="grid gap-2">
					<Label for="hub-key-name">Name</Label>
					<Input
						id="hub-key-name"
						name="name"
						placeholder="e.g. training cluster"
						autocomplete="off"
						maxlength={128}
						aria-invalid={!!mutation.error}
					/>
				</div>
				<div class="grid gap-2">
					<Label for="hub-key-org">Organization</Label>
					<input type="hidden" name="org" value={org} />
					<Select.Root type="single" bind:value={org}>
						<Select.Trigger id="hub-key-org" class="w-full">{orgLabel(org)}</Select.Trigger>
						<Select.Content>
							<Select.Item value={ANY} label="Every organization">Every organization</Select.Item>
							{#each orgs as o (o.name)}
								<Select.Item value={o.name} label={o.display_name}>{o.display_name}</Select.Item>
							{/each}
						</Select.Content>
					</Select.Root>
				</div>
				<div class="flex items-start gap-2.5">
					<Checkbox
						id="hub-key-read-only"
						name="read_only"
						bind:checked={readOnly}
						class="mt-0.5"
					/>
					<Label for="hub-key-read-only" class="block leading-snug font-normal">
						Read only
						<span class="text-muted-foreground">— it can’t write versions, tags or cards.</span>
					</Label>
				</div>
				<FormError error={mutation.error} />
				<Dialog.Footer>
					<Dialog.Close>
						{#snippet child({ props })}
							<Button {...props} variant="outline">Cancel</Button>
						{/snippet}
					</Dialog.Close>
					<Button type="submit" disabled={mutation.pending}>
						{mutation.pending ? 'Creating…' : 'Create key'}
					</Button>
				</Dialog.Footer>
			</form>
		{/if}
	</Dialog.Content>
</Dialog.Root>
