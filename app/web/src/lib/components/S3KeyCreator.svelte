<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import KeyRoundIcon from '@lucide/svelte/icons/key-round';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import * as Select from '#lib/components/ui/select/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import { expiryChoices } from '#lib/s3.ts';
	import type { S3KeyCreated } from '#lib/types.ts';
	import { dateTime, formText } from '#lib/utils.ts';
	import AnchoredForm from './AnchoredForm.svelte';
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
		done: (data) => {
			created = data.s3Key;
			open = false;
		}
	});
	let open = $state(false);
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
	<!-- In a small form opened from the button, as a wandb key is (Harry): the same fields. -->
	<AnchoredForm
		bind:open
		title="New S3 key"
		submit="Create key"
		action="/account?/createS3Key"
		enhance={mutation.enhance}
		pending={mutation.pending}
		error={mutation.error}
		disabled={!org}
		align="start"
		wide
		onclosed={() => mutation.reset()}
	>
		{#snippet trigger(props)}
			<Button {...props} variant="outline"><KeyRoundIcon /> Create S3 key</Button>
		{/snippet}
		<div class="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-3 gap-y-2 text-[13px]">
			<Label for="s3-key-name" class="text-[13px] font-normal text-muted-foreground">Name</Label>
			<Input
				id="s3-key-name"
				name="name"
				class="h-8"
				placeholder="e.g., notebook"
				autocomplete="off"
				maxlength={128}
			/>
			<Label for="s3-key-org" class="text-[13px] font-normal text-muted-foreground">
				Organization
			</Label>
			<div>
				<input type="hidden" name="org" value={org} />
				<Select.Root type="single" bind:value={org}>
					<Select.Trigger id="s3-key-org" class="w-full">{orgLabel(org)}</Select.Trigger>
					<Select.Content>
						{#each orgs as option (option.name)}
							<Select.Item value={option.name} label={option.display_name}>
								{option.display_name}
							</Select.Item>
						{/each}
					</Select.Content>
				</Select.Root>
			</div>
			<Label for="s3-key-expiry" class="text-[13px] font-normal text-muted-foreground">
				Expires after
			</Label>
			<div>
				<input type="hidden" name="ttl" value={ttl} />
				<Select.Root type="single" bind:value={ttl}>
					<Select.Trigger id="s3-key-expiry" class="w-full"
						>{chosen?.label ?? 'Choose'}</Select.Trigger
					>
					<Select.Content>
						{#each choices as choice (choice.label)}
							<Select.Item
								value={String(choice.seconds ?? 'never')}
								label={choice.label}
								disabled={choice.disabled !== null}
								{@attach hint(choice.disabled)}
							>
								{choice.label}
							</Select.Item>
						{/each}
					</Select.Content>
				</Select.Root>
			</div>
			<Label for="s3-key-permissions" class="text-[13px] font-normal text-muted-foreground">
				Permissions
			</Label>
			<div>
				{#if permissions === 'read'}<input type="hidden" name="read_only" value="on" />{/if}
				<Select.Root type="single" bind:value={permissions}>
					<Select.Trigger id="s3-key-permissions" class="w-full"
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
	</AnchoredForm>
{/if}
