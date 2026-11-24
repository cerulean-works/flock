<script lang="ts">
	import PlugIcon from '@lucide/svelte/icons/plug';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import ConnectPanel from './ConnectPanel.svelte';

	interface Props {
		org: string;
		endpoint: string;
		region: string;
		/** A bucket to name in the examples. */
		bucket: string;
	}

	let { org, endpoint, region, bucket }: Props = $props();
</script>

<!-- The Storage page's "Connect" button, opening how to reach the buckets from any S3 client. -->
<Dialog.Root>
	<Dialog.Trigger>
		{#snippet child({ props })}
			<Button {...props} variant="ghost">
				<PlugIcon /> <span class="max-sm:sr-only">Connect</span>
			</Button>
		{/snippet}
	</Dialog.Trigger>
	<Dialog.Content class="max-h-[calc(100dvh-4rem)] overflow-y-auto sm:max-w-xl">
		<Dialog.Header>
			<Dialog.Title>Connect</Dialog.Title>
			<Dialog.Description>
				Any S3 client can connect to this endpoint with path-style addressing. The commands below
				sign in with your Flock account and renew their keys automatically.
			</Dialog.Description>
		</Dialog.Header>
		<ConnectPanel {org} {endpoint} {region} {bucket} />
	</Dialog.Content>
</Dialog.Root>
