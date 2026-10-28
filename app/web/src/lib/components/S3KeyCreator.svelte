<script lang="ts">
	import KeyRoundIcon from '@lucide/svelte/icons/key-round';
	import { enhance } from '$app/forms';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import * as Select from '#lib/components/ui/select/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import { expiryChoices } from '#lib/s3.ts';
	import type { S3KeyCreated } from '#lib/types.ts';
	import { dateTime, formText } from '#lib/utils.ts';
	import FormError from './FormError.svelte';
	import SecretField from './SecretField.svelte';

	interface Props {
		/** The orgs a key can be for, with their maximum key lifetime (null: none). */
		orgs: { name: string; display_name: string; s3_key_max_ttl: number | null }[];
		/** The org picked at first: the user's default. */
		initialOrg: string | null;
	}

	let { orgs, initialOrg }: Props = $props();

	let org = $derived(initialOrg ?? orgs[0]?.name ?? '');
	const max = $derived(orgs.find((o) => o.name === org)?.s3_key_max_ttl ?? null);
	const choices = $derived(expiryChoices(max));
	// Seconds as text, or "never": the longest the org allows, up to 30 days.
	let ttl = $state(String(30 * 86_400));
	const chosen = $derived(choices.find((c) => String(c.seconds ?? 'never') === ttl));
	$effect(() => {
		// A choice the newly picked org doesn't allow falls back to its longest one that fits.
		if (!chosen || chosen.disabled) {
			const fallback = choices.filter((c) => !c.disabled).at(-1);
			ttl = String(fallback?.seconds ?? 'never');
		}
	});

	// Shown once: Flock stores no secret.
	let created = $state<S3KeyCreated | null>(null);
	const mutation = new Mutation<{ s3Key: S3KeyCreated }>({
		validate: (form) => (formText(form, 'name') ? null : 'Name the key'),
		success: ({ s3Key }) => `Created S3 key “${s3Key.name}”`,
		done: (data) => (created = data.s3Key)
	});
	// Read and write unless the key is made read only.
	let permissions = $state<'write' | 'read'>('write');
	const PERMISSIONS = { write: 'Read & write', read: 'Read only' } as const;

	const orgLabel = (name: string) => orgs.find((o) => o.name === name)?.display_name ?? name;
</script>

{#if created}
	<div class="grid gap-3 rounded-lg border border-divider p-4">
		<p>
			Created <span class="font-medium">{created.name}</span> for {orgLabel(created.org ?? '')}.
			{created.read_only ? 'It’s read-only and ' : 'It '}{created.expires_at === null
				? 'never expires.'
				: `expires ${dateTime(created.expires_at)}.`}
		</p>
		<SecretField label="Access key ID" value={created.access_key_id} copy />
		<SecretField label="Secret access key" value={created.secret_access_key} copy />
		<SecretField label="Endpoint" value={created.endpoint} />
		<p class="text-[13px] text-muted-foreground">Copy the secret now. It won’t be shown again.</p>
		<div>
			<Button variant="outline" onclick={() => (created = null)}>
				<KeyRoundIcon /> Create another
			</Button>
		</div>
	</div>
{:else}
	<form
		method="POST"
		action="/account?/createS3Key"
		class="grid gap-3"
		use:enhance={mutation.enhance}
	>
		<div class="flex flex-wrap items-end gap-2">
			<div class="grid min-w-48 flex-1 gap-2">
				<Label for="s3-key-name">Name</Label>
				<Input
					id="s3-key-name"
					name="name"
					placeholder="e.g. notebook"
					autocomplete="off"
					maxlength={128}
				/>
			</div>
			<div class="grid gap-2">
				<Label for="s3-key-org">Organization</Label>
				<input type="hidden" name="org" value={org} />
				<Select.Root type="single" bind:value={org}>
					<Select.Trigger id="s3-key-org" class="w-44">{orgLabel(org)}</Select.Trigger>
					<Select.Content>
						{#each orgs as option (option.name)}
							<Select.Item value={option.name} label={option.display_name}>
								{option.display_name}
							</Select.Item>
						{/each}
					</Select.Content>
				</Select.Root>
			</div>
			<div class="grid gap-2">
				<Label for="s3-key-expiry">Expires after</Label>
				<input type="hidden" name="ttl" value={ttl} />
				<Select.Root type="single" bind:value={ttl}>
					<Select.Trigger id="s3-key-expiry" class="w-32"
						>{chosen?.label ?? 'Choose'}</Select.Trigger
					>
					<Select.Content>
						{#each choices as choice (choice.label)}
							<Select.Item
								value={String(choice.seconds ?? 'never')}
								label={choice.label}
								disabled={choice.disabled !== null}
								title={choice.disabled ?? undefined}
							>
								{choice.label}
							</Select.Item>
						{/each}
					</Select.Content>
				</Select.Root>
			</div>
			<div class="grid gap-2">
				<Label for="s3-key-permissions">Permissions</Label>
				{#if permissions === 'read'}<input type="hidden" name="read_only" value="on" />{/if}
				<Select.Root type="single" bind:value={permissions}>
					<Select.Trigger id="s3-key-permissions" class="w-36"
						>{PERMISSIONS[permissions]}</Select.Trigger
					>
					<Select.Content>
						{#each Object.entries(PERMISSIONS) as [value, label] (value)}
							<Select.Item {value} {label}>{label}</Select.Item>
						{/each}
					</Select.Content>
				</Select.Root>
			</div>
		</div>
		<FormError error={mutation.error} />
		<div>
			<Button type="submit" variant="outline" disabled={mutation.pending || !org}>
				<KeyRoundIcon />
				{mutation.pending ? 'Creating…' : 'Create S3 key'}
			</Button>
		</div>
	</form>
{/if}
