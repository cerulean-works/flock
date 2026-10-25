<script lang="ts">
	import EllipsisIcon from '@lucide/svelte/icons/ellipsis';
	import HistoryIcon from '@lucide/svelte/icons/history';
	import MessageSquareIcon from '@lucide/svelte/icons/message-square';
	import ScissorsIcon from '@lucide/svelte/icons/scissors';
	import ShrinkIcon from '@lucide/svelte/icons/shrink';
	import TagIcon from '@lucide/svelte/icons/tag';
	import XIcon from '@lucide/svelte/icons/x';
	import { enhance } from '$app/forms';
	import Avatar from '#lib/components/Avatar.svelte';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import { opLabel, versionDelta } from '#lib/datasets.ts';
	import type { DatasetVersion, Role } from '#lib/types.ts';
	import { bytes, count, dateTime, relativeTime } from '#lib/utils.ts';
	import ActionDialog, { type Field } from './ActionDialog.svelte';

	interface Props {
		versions: DatasetVersion[];
		/** Versions in all, when there are more than listed. */
		total: number;
		latest: number | null;
		/** The version the page shows. */
		current: number | null;
		role: Role | null;
		/** The signed-in user, who may describe their own versions. */
		me: number;
		now: number;
		/** The page with this version in view. */
		viewHref: (version: number) => string;
	}

	let { versions, total, latest, current, role, me, now, viewHref }: Props = $props();

	const member = $derived(role != null);
	const admin = $derived(role === 'admin');

	interface Dialog {
		title: string;
		description: string;
		action: string;
		fields?: Record<string, string | number>;
		input?: Field;
		confirm: string;
		pending: string;
		success: (data: Record<string, unknown>) => string;
		destructive?: boolean;
	}

	let dialog = $state<Dialog | null>(null);
	let dialogOpen = $state(false);
	function ask(d: Dialog) {
		dialog = d;
		dialogOpen = true;
	}

	const tagDialog = (v: number): Dialog => ({
		title: `Tag version ${v}`,
		description: 'A tag names a version, so scripts can load it by name. A tag in use moves here.',
		action: '?/tag',
		fields: { version: v },
		input: { name: 'tag', label: 'Tag', placeholder: 'v1.0', required: true },
		confirm: 'Tag',
		pending: 'Tagging…',
		success: () => `Tagged version ${v}`
	});

	const restoreDialog = (v: number): Dialog => ({
		title: `Restore version ${v}?`,
		description: `Makes a new version with the contents of version ${v}. Later versions stay in the history.`,
		action: '?/restore',
		fields: { version: v },
		input: { name: 'message', label: 'Message', hint: 'optional', placeholder: `Restore v${v}` },
		confirm: 'Restore',
		pending: 'Restoring…',
		success: (d) => `Restored version ${v} as version ${d.version}`
	});

	const messageDialog = (v: number): Dialog => ({
		title: `Describe version ${v}`,
		description: 'For versions saved without a message. It can be set only once.',
		action: '?/message',
		fields: { version: v },
		input: { name: 'message', label: 'Message', required: true },
		confirm: 'Save',
		pending: 'Saving…',
		success: () => 'Message saved'
	});

	const optimizeDialog: Dialog = {
		title: 'Optimize the dataset?',
		description:
			'Compacts small fragments into larger ones, as a new version with the same rows. Reads get faster; nothing is lost.',
		action: '?/optimize',
		confirm: 'Optimize',
		pending: 'Optimizing…',
		success: (d) =>
			d.optimized ? `Optimized as version ${d.optimized}` : 'Nothing needed compacting'
	};

	const pruneDialog: Dialog = {
		title: 'Prune old versions?',
		description:
			'Deletes the files only older versions use. The latest version and tagged ones stay; pruned versions stay in the history but can’t be read or restored. There is no undo.',
		action: '?/prune',
		input: {
			name: 'days',
			label: 'Versions older than (days)',
			type: 'number',
			value: '30',
			min: 0,
			step: 'any',
			required: true
		},
		confirm: 'Prune',
		pending: 'Pruning…',
		success: (d) =>
			`Pruned ${count(Number(d.pruned), 'version')}, freeing ${bytes(Number(d.bytes) || 0)}`,
		destructive: true
	};

	const untag = new Mutation({ success: (d) => `Removed tag ${d.untagged}`, toastErrors: true });

	const canDescribe = (v: DatasetVersion) =>
		!v.message && !v.pruned && (admin || (member && v.author?.id === me));
</script>

<!-- Members may optimize (a new version, nothing lost); only admins prune. -->
{#if member}
	<div class="flex h-12 items-center gap-2 border-b border-divider px-5">
		<p class="text-[13px] text-muted-foreground max-sm:hidden">
			{count(total, 'version')}
		</p>
		<div class="ml-auto flex gap-2">
			<Button variant="outline" size="sm" onclick={() => ask(optimizeDialog)}>
				<ShrinkIcon /> Optimize
			</Button>
			{#if admin}
				<Button variant="outline" size="sm" onclick={() => ask(pruneDialog)}>
					<ScissorsIcon /> Prune…
				</Button>
			{/if}
		</div>
	</div>
{/if}

{#if !versions.length}
	<EmptyState>No versions yet: the dataset’s first write makes version 1.</EmptyState>
{:else}
	<ol class="mx-auto max-w-200 px-5 py-4">
		{#each versions as v, i (v.version)}
			{@const delta = versionDelta(v, versions[i + 1])}
			<li
				class={[
					'grid grid-cols-[56px_minmax(0,1fr)_auto] gap-x-4 border-b border-divider py-4',
					v.version === current && 'bg-selection/30'
				]}
			>
				<div class="pl-2">
					<span class="font-medium tabular-nums">v{v.version}</span>
				</div>
				<div class="min-w-0">
					<div class="flex flex-wrap items-center gap-1.5">
						<span class="text-[13px] text-muted-foreground">{opLabel(v.op)}</span>
						{#if v.version === latest}
							<Badge variant="secondary">Latest</Badge>
						{/if}
						{#if v.pruned}
							<Badge variant="outline" class="text-muted-foreground" title="Its files were deleted">
								Pruned
							</Badge>
						{/if}
						{#each v.tags as tag (tag)}
							<Badge variant="secondary" class="gap-0.5 font-mono font-normal">
								<TagIcon />
								{tag}
								{#if member}
									<form method="POST" action="?/untag" class="contents" use:enhance={untag.enhance}>
										<input type="hidden" name="tag" value={tag} />
										<button
											type="submit"
											class="-mr-1 grid size-4 place-items-center rounded-full text-muted-foreground hover:text-foreground"
											aria-label="Remove tag {tag}"
											title="Remove tag"
										>
											<XIcon class="size-3!" />
										</button>
									</form>
								{/if}
							</Badge>
						{/each}
					</div>
					{#if v.message}
						<p class="mt-1 font-serif text-[17px] leading-snug">{v.message}</p>
					{/if}
					<p class="mt-1.5 flex flex-wrap items-center gap-x-2 text-[13px] text-muted-foreground">
						{#if v.author}
							<span class="flex items-center gap-1.5">
								<Avatar name={v.author.name} src={v.author.avatar_url} class="size-5 text-[9px]" />
								<span title={v.author.email}>{v.author.name}</span>
							</span>
							<span aria-hidden="true">·</span>
						{/if}
						<span title={dateTime(v.created_at)}>{relativeTime(v.created_at, now)}</span>
						{#if delta}
							<span aria-hidden="true">·</span>
							<span class="tabular-nums">{delta}</span>
						{/if}
					</p>
				</div>
				<div class="flex items-start gap-1">
					<Button
						variant="ghost"
						size="sm"
						href={viewHref(v.version)}
						disabled={v.pruned}
						class="aria-disabled:pointer-events-none aria-disabled:opacity-40"
					>
						View
					</Button>
					{#if member && !v.pruned}
						<DropdownMenu.Root>
							<DropdownMenu.Trigger>
								{#snippet child({ props })}
									<Button
										{...props}
										variant="ghost"
										size="icon-sm"
										aria-label="Version {v.version} actions"
									>
										<EllipsisIcon />
									</Button>
								{/snippet}
							</DropdownMenu.Trigger>
							<DropdownMenu.Content align="end" class="w-48">
								<DropdownMenu.Item onSelect={() => ask(tagDialog(v.version))}>
									<TagIcon /> Tag…
								</DropdownMenu.Item>
								{#if v.version !== latest}
									<DropdownMenu.Item onSelect={() => ask(restoreDialog(v.version))}>
										<HistoryIcon /> Restore…
									</DropdownMenu.Item>
								{/if}
								{#if canDescribe(v)}
									<DropdownMenu.Item onSelect={() => ask(messageDialog(v.version))}>
										<MessageSquareIcon /> Add a message…
									</DropdownMenu.Item>
								{/if}
							</DropdownMenu.Content>
						</DropdownMenu.Root>
					{/if}
				</div>
			</li>
		{/each}
	</ol>
	{#if total > versions.length}
		<p class="px-5 pb-6 text-center text-[13px] text-muted-foreground">
			The {versions.length} latest of {count(total, 'version')}.
		</p>
	{/if}
{/if}

{#if dialog}
	<ActionDialog bind:open={dialogOpen} {...dialog} />
{/if}
