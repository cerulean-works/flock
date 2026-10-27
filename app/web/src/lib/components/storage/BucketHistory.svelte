<script lang="ts">
	import * as Sheet from '#lib/components/ui/sheet/index.js';
	import { eventVia } from '#lib/s3.ts';
	import { bucketApi } from '#lib/storage.ts';
	import type { BucketDetail } from '#lib/types.ts';
	import { dateTime } from '#lib/utils.ts';

	interface Props {
		open: boolean;
		org: string;
		/** The bucket whose history to show; read when the sheet opens. */
		bucket: string;
	}

	let { open = $bindable(), org, bucket }: Props = $props();

	const EVENTS = { created: 'Created', deleted: 'Deleted', purged: 'Emptied and removed' };

	let detail = $state<BucketDetail | null>(null);
	let error = $state<string | null>(null);

	$effect(() => {
		if (!open || !bucket) return;
		const controller = new AbortController();
		detail = null;
		error = null;
		fetch(bucketApi(org, bucket, ''), { signal: controller.signal })
			.then(async (res) => {
				const json = await res.json();
				if (!res.ok) throw new Error(json.message ?? `Flock answered ${res.status}`);
				detail = json as BucketDetail;
			})
			.catch((e: Error) => {
				if (!controller.signal.aborted) error = e.message;
			});
		return () => controller.abort();
	});
</script>

<Sheet.Root bind:open>
	<Sheet.Content side="right" class="gap-0 p-0 sm:max-w-md">
		<Sheet.Header class="border-b border-divider px-5 py-4">
			<Sheet.Title class="font-mono text-[15px] font-normal">{bucket}</Sheet.Title>
			<Sheet.Description>Who created and deleted the bucket, and how.</Sheet.Description>
		</Sheet.Header>
		<div class="overflow-y-auto px-5 py-4 text-[13px]">
			{#if error}
				<p class="text-destructive">{error}</p>
			{:else if !detail}
				<p class="text-muted-foreground">Loading…</p>
			{:else}
				<p class="text-muted-foreground">
					On the S3 server: <span class="font-mono text-[12.5px]">{detail.upstream_name}</span>
				</p>
				<ol class="mt-3 grid gap-2">
					{#each detail.events as event, i (i)}
						<li>
							<p>
								{EVENTS[event.kind]}{event.user ? ` by ${event.user.name}` : ''}
								<span class="text-muted-foreground">{eventVia(event.detail)}</span>
							</p>
							<p class="text-muted-foreground tabular-nums">{dateTime(event.at)}</p>
						</li>
					{/each}
				</ol>
			{/if}
		</div>
	</Sheet.Content>
</Sheet.Root>
