<script lang="ts">
	import { resolve } from '$app/paths';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { datasetUri, readSnippet } from '#lib/datasets.ts';
	import type { S3Config } from '#lib/types.ts';
	import CopyBlock from '../CopyBlock.svelte';
	import CopyButton from '../CopyButton.svelte';

	interface Props {
		open: boolean;
		path: string;
		/** The S3 proxy clients connect to. */
		s3: S3Config;
	}

	let { open = $bindable(), path, s3 }: Props = $props();

	const uri = $derived(datasetUri(path));
</script>

<!-- Where the dataset lives in S3, and how to open it with Lance from outside Flock. -->
<Dialog.Root bind:open>
	<Dialog.Content class="sm:max-w-xl">
		<Dialog.Header>
			<Dialog.Title>Access in S3</Dialog.Title>
			<Dialog.Description>Open this dataset with Lance or any other S3 client.</Dialog.Description>
		</Dialog.Header>
		<dl
			class="grid min-w-0 grid-cols-[auto_minmax(0,1fr)] items-center gap-x-4 gap-y-1 text-[13px]"
		>
			<dt class="text-muted-foreground">S3 URI</dt>
			<dd class="flex min-w-0 items-center gap-1">
				<span class="truncate font-mono text-[12.5px]">{uri}</span>
				<CopyButton text={uri} label="Copy S3 URI" />
			</dd>
			<dt class="text-muted-foreground">Endpoint</dt>
			<dd class="flex min-w-0 items-center gap-1">
				<span class="truncate font-mono text-[12.5px]">{s3.endpoint}</span>
				<CopyButton text={s3.endpoint} label="Copy endpoint" />
			</dd>
		</dl>
		<div class="grid min-w-0 gap-1.5">
			<p class="text-[13px] text-muted-foreground">Python</p>
			<CopyBlock code={readSnippet(path, s3.endpoint, s3.region)} />
		</div>
		<p class="text-[13px] text-muted-foreground">
			You can create S3 keys on your
			<a
				class="text-link underline-offset-3 hover:underline"
				href="{resolve('/(app)/account')}#s3-keys">account page</a
			>.
		</p>
	</Dialog.Content>
</Dialog.Root>
