<script lang="ts">
	import { resolve } from '$app/paths';
	import CopyBlock from '../CopyBlock.svelte';
	import CopyButton from '../CopyButton.svelte';

	interface Props {
		org: string;
		endpoint: string;
		region: string;
		/** A bucket to name in the examples. */
		bucket: string;
	}

	let { org, endpoint, region, bucket }: Props = $props();

	const snippets = $derived([
		{
			label: 'AWS CLI, with keys that renew themselves',
			code: `flock s3 configure --org ${org}\naws s3 ls s3://${bucket}/ --profile flock`
		},
		{
			label: 'Environment variables for this shell',
			code: `eval "$(flock s3 credentials --org ${org})"\naws s3 ls s3://${bucket}/`
		},
		{
			label: 'Python',
			code: [
				'from flock.hub import s3',
				'',
				`creds = s3.credentials("${org}")`,
				'client = creds.boto3_client()',
				`client.list_objects_v2(Bucket="${bucket}")`
			].join('\n')
		}
	]);
</script>

<!-- How to reach the org's buckets from code: the proxy's address, and ready-to-copy snippets. -->
<div class="bg-card px-4 py-4">
	<dl class="grid grid-cols-[auto_1fr] items-center gap-x-4 gap-y-1 text-[13px]">
		<dt class="text-muted-foreground">Endpoint</dt>
		<dd class="flex min-w-0 items-center gap-1">
			<span class="truncate font-mono text-[12.5px]">{endpoint}</span>
			<CopyButton text={endpoint} label="Copy endpoint" />
		</dd>
		<dt class="text-muted-foreground">Region</dt>
		<dd class="font-mono text-[12.5px]">{region}</dd>
	</dl>
	<div class="mt-4 grid gap-3">
		{#each snippets as s (s.label)}
			<div class="grid gap-1.5">
				<p class="text-[13px] text-muted-foreground">{s.label}</p>
				<CopyBlock code={s.code} />
			</div>
		{/each}
	</div>
	<p class="mt-3 text-[13px] text-muted-foreground">
		Tools that can’t run <code class="font-mono text-[0.9em]">flock</code> need a long-lived key.
		You can create one on your
		<a
			class="text-link underline-offset-3 hover:underline"
			href="{resolve('/(app)/account')}#s3-keys">account page</a
		>.
	</p>
</div>
