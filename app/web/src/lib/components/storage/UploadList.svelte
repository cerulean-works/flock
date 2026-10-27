<script lang="ts">
	import XIcon from '@lucide/svelte/icons/x';
	import { Button } from '#lib/components/ui/button/index.js';
	import { nameOf } from '#lib/storage.ts';
	import type { UploadQueue } from '#lib/uploads.svelte.ts';
	import { bytes, count } from '#lib/utils.ts';

	let { queue }: { queue: UploadQueue } = $props();

	const STATES = {
		queued: 'Waiting',
		uploading: '',
		done: 'Done',
		failed: 'Failed',
		cancelled: 'Cancelled'
	};
	const active = $derived(queue.active.length);
</script>

<!-- One row per file: its progress, and a way to cancel it while it runs. -->
{#if queue.uploads.length}
	<section class="bg-card px-4 py-3" aria-label="Uploads">
		<div class="flex h-7 items-center gap-2 text-[13px]">
			<h2 class="font-medium">
				{active ? `Uploading ${count(active, 'file')}` : 'Uploads'}
			</h2>
			{#if queue.uploads.length > active}
				<Button
					variant="ghost"
					size="xs"
					class="ml-auto text-muted-foreground"
					onclick={() => queue.clear()}
				>
					Clear finished
				</Button>
			{/if}
		</div>
		<ul class="mt-1 grid gap-1.5">
			{#each queue.uploads as upload (upload.id)}
				{@const share = upload.size ? upload.sent / upload.size : 1}
				<li class="grid grid-cols-[1fr_auto] items-center gap-x-3 text-[13px]">
					<div class="min-w-0">
						<div class="flex items-baseline gap-2">
							<span class="truncate font-mono text-[12.5px]" title={upload.key}
								>{nameOf(upload.key)}</span
							>
							<span
								class={[
									'ml-auto flex-none tabular-nums',
									upload.state === 'failed' ? 'text-destructive' : 'text-muted-foreground'
								]}
							>
								{#if upload.state === 'uploading'}
									{bytes(upload.sent)} of {bytes(upload.size)}{upload.multipart
										? ' · in parts'
										: ''}
								{:else}
									{STATES[upload.state]}
								{/if}
							</span>
						</div>
						{#if upload.state === 'uploading' || upload.state === 'queued'}
							<div
								class="mt-1 h-1.5 overflow-hidden rounded-full bg-muted"
								role="progressbar"
								aria-label="Uploading {nameOf(upload.key)}"
								aria-valuemin={0}
								aria-valuemax={100}
								aria-valuenow={Math.round(share * 100)}
							>
								<div
									class="h-full bg-primary transition-[width]"
									style:width="{share * 100}%"
								></div>
							</div>
						{:else if upload.error}
							<p class="text-destructive">{upload.error}</p>
						{/if}
					</div>
					{#if upload.state === 'uploading' || upload.state === 'queued'}
						<Button
							variant="ghost"
							size="icon-xs"
							class="text-muted-foreground"
							aria-label="Cancel uploading {nameOf(upload.key)}"
							title="Cancel"
							onclick={() => upload.cancel()}
						>
							<XIcon />
						</Button>
					{:else}
						<span class="size-6"></span>
					{/if}
				</li>
			{/each}
		</ul>
	</section>
{/if}
