<script lang="ts">
	import FileIcon from '@lucide/svelte/icons/file';
	import { folderHref } from '#lib/models.ts';
	import type { ModelVersionDetail } from '#lib/types.ts';
	import { bytes } from '#lib/utils.ts';

	let { org, version }: { org: string; version: ModelVersionDetail } = $props();
</script>

<table class="w-full table-fixed border-collapse text-[13px]">
	<thead>
		<tr class="h-10 border-b text-left text-xs text-muted-foreground">
			<th class="px-2 font-medium">Path</th>
			<th class="w-28 px-2 text-right font-medium">Size</th>
			<th class="hidden w-[24rem] px-2 font-medium md:table-cell">sha256</th>
		</tr>
	</thead>
	<tbody>
		{#each version.files as file (file.path)}
			<tr class="h-10 border-b border-divider hover:bg-muted">
				<td class="truncate px-2">
					<a
						class="flex items-center gap-2 underline-offset-3 hover:text-link hover:underline"
						href={folderHref(org, `${version.prefix}${file.path}`)}
					>
						<FileIcon class="size-4 flex-none text-muted-foreground" />
						<span class="truncate font-mono text-[12.5px]" title={file.path}>{file.path}</span>
					</a>
				</td>
				<td class="px-2 text-right text-muted-foreground tabular-nums">{bytes(file.size)}</td>
				<td
					class="hidden truncate px-2 font-mono text-[12px] text-muted-foreground md:table-cell"
					title={file.sha256}
				>
					{file.sha256}
				</td>
			</tr>
		{/each}
	</tbody>
</table>
