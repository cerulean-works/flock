<script lang="ts">
	import { accountHref } from '#lib/settings.ts';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { datasetUri, pyString, readSnippet } from '#lib/datasets.ts';
	import type { Scope } from '#lib/scope.ts';
	import type { S3Config } from '#lib/types.ts';
	import CopyBlock from '../CopyBlock.svelte';
	import CopyButton from '../CopyButton.svelte';

	interface Props {
		open: boolean;
		scope: Scope;
		/** Relative to the scope. */
		path: string;
		/** Where it really is in flock-datasets (under `-projects/` in a project, D160). */
		objectKey?: string;
		/** The S3 proxy clients connect to. */
		s3: S3Config;
	}

	let { open = $bindable(), scope, path, objectKey = path, s3 }: Props = $props();

	const uri = $derived(datasetUri(objectKey));
	// A project's dataset is found by name, so code never holds its project's number (D160).
	const locate = $derived(
		scope.project === null
			? null
			: [
					'import lance',
					'from flock.hub import datasets',
					'',
					`where = datasets.location(${pyString(path)}, project=${pyString(scope.project)})`,
					'ds = lance.dataset(where.uri, storage_options=where.storage_options)'
				].join('\n')
	);
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
			<CopyBlock code={locate ?? readSnippet(path, s3.endpoint, s3.region, uri)} />
		</div>
		<p class="text-[13px] text-muted-foreground">
			You can create S3 keys on your
			<a
				class="text-link underline-offset-3 hover:underline"
				href={accountHref('keys', { keys: 's3' })}>account page</a
			>.
		</p>
	</Dialog.Content>
</Dialog.Root>
