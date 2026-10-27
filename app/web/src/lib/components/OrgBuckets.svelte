<script lang="ts">
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import { enhance } from '$app/forms';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import { bucketNameError, eventVia } from '#lib/s3.ts';
	import type { BucketDetail } from '#lib/types.ts';
	import { dateTime, formText, relativeTime } from '#lib/utils.ts';
	import DeleteBucketDialog from './DeleteBucketDialog.svelte';
	import FormError from './FormError.svelte';

	interface Props {
		buckets: BucketDetail[];
		/** The longest name this server allows. */
		nameLimit: number;
		endpoint: string;
		admin: boolean;
		now: number;
	}

	let { buckets, nameLimit, endpoint, admin, now }: Props = $props();

	let createForm = $state<HTMLFormElement>();
	const creating = new Mutation<{ bucket: string }>({
		validate: (form) => bucketNameError(formText(form, 'name'), nameLimit),
		success: ({ bucket }) => `Created bucket ${bucket}`,
		done: () => createForm?.reset()
	});
	let deleting = $state<string | null>(null);

	const EVENTS = { created: 'Created', deleted: 'Deleted', purged: 'Emptied and removed' };
</script>

<p class="mt-2 max-w-[62ch] text-muted-foreground">
	Shared S3 storage for everyone in the organization, at
	<span class="font-mono text-[0.9em]">{endpoint}</span>. Members read, write and delete objects;
	only admins delete buckets.
</p>

{#if buckets.length}
	<ul class="mt-4 border-t border-divider">
		{#each buckets as bucket (bucket.name)}
			<li class="border-b border-divider">
				<details class="group">
					<summary
						class="flex min-h-12 cursor-pointer list-none items-center gap-3 py-2 [&::-webkit-details-marker]:hidden"
					>
						<ChevronRightIcon
							class="size-4 flex-none text-muted-foreground transition-transform group-open:rotate-90"
						/>
						<span class="min-w-0 flex-1 truncate font-mono text-[13.5px]">{bucket.name}</span>
						<span
							class="hidden text-[13px] text-muted-foreground sm:block"
							title={dateTime(bucket.created_at)}
						>
							{bucket.created_by?.name ?? 'Someone since removed'} · {relativeTime(
								bucket.created_at,
								now
							)}
						</span>
						{#if admin}
							<Button
								variant="ghost"
								size="icon-sm"
								class="text-muted-foreground hover:text-destructive"
								aria-label="Delete bucket {bucket.name}"
								title="Delete the bucket and everything in it"
								onclick={(e: MouseEvent) => {
									e.preventDefault();
									deleting = bucket.name;
								}}
							>
								<Trash2Icon />
							</Button>
						{/if}
					</summary>
					<div class="grid gap-1 pb-3 pl-7 text-[13px]">
						<p class="text-muted-foreground">
							On the S3 server: <span class="font-mono text-[12.5px]">{bucket.upstream_name}</span>
						</p>
						<ol class="grid gap-0.5">
							{#each bucket.events as event, i (i)}
								<li>
									<span title={dateTime(event.at)}>{dateTime(event.at)}</span> ·
									{EVENTS[event.kind]}{event.user ? ` by ${event.user.name}` : ''}
									<span class="text-muted-foreground">{eventVia(event.detail)}</span>
								</li>
							{/each}
						</ol>
					</div>
				</details>
			</li>
		{/each}
	</ul>
{:else}
	<p class="mt-3 text-muted-foreground">No buckets yet.</p>
{/if}

<form
	bind:this={createForm}
	method="POST"
	action="?/createBucket"
	class="mt-4 grid max-w-120 gap-2"
	use:enhance={creating.enhance}
>
	<input type="hidden" name="limit" value={nameLimit} />
	<div class="flex gap-2">
		<Input
			name="name"
			placeholder="e.g. checkpoints"
			aria-label="Bucket name"
			autocomplete="off"
			spellcheck="false"
			maxlength={nameLimit}
			class="font-mono text-[13px]"
		/>
		<Button type="submit" variant="outline" disabled={creating.pending}>
			<PlusIcon />
			{creating.pending ? 'Creating…' : 'Create bucket'}
		</Button>
	</div>
	<FormError error={creating.error} />
	<p class="text-[13px] text-muted-foreground">
		Lowercase letters, digits, hyphens and dots, up to {nameLimit} characters. Names can’t be changed
		later.
	</p>
</form>

{#if admin}
	<DeleteBucketDialog
		bind:open={() => deleting !== null, (o) => (deleting = o ? deleting : null)}
		bucket={deleting ?? ''}
	/>
{/if}
