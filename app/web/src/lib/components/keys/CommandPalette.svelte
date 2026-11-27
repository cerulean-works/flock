<script lang="ts">
	import { Command } from 'bits-ui';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { datasetsHref } from '#lib/datasets.ts';
	import { groupHref, groupsHref, type GroupList } from '#lib/groups.ts';
	import { keyboard } from '#lib/keys.svelte.ts';
	import { modelsHref } from '#lib/models.ts';
	import { storageHref } from '#lib/storage.ts';
	import { setTheme } from '#lib/theme.svelte.ts';
	import type { ProjectInfo } from '#lib/types.ts';
	import { entityHref, orgHref, projectHref } from '#lib/workspace/url.ts';

	let { open = $bindable(false) }: { open?: boolean } = $props();

	const shell = $derived(page.data.shell);
	const org = $derived(shell?.access === 'public' ? null : (shell?.org ?? null));
	const project = $derived(page.params.project ?? null);
	const groups = $derived((page.data.groups as GroupList | null | undefined)?.groups ?? []);

	interface Entry {
		label: string;
		/** More words it answers to. */
		keywords?: string[];
		href?: string;
		run?: () => void;
		hint?: string;
	}

	const sections = $derived.by((): [string, Entry[]][] => {
		const go: Entry[] = [];
		if (org) {
			go.push({ label: 'Runs', href: entityHref(org), hint: 'G R' });
			if (shell?.s3) {
				go.push({ label: 'Datasets', href: datasetsHref(org), hint: 'G D' });
				go.push({ label: 'Models', href: modelsHref(org), hint: 'G M' });
				go.push({ label: 'Storage', href: storageHref(org) });
			}
			go.push({ label: 'Settings and members', href: orgHref(org), hint: 'G S' });
		}
		go.push({ label: 'Account and keys', href: resolve('/(app)/account') });

		const out: [string, Entry[]][] = [];
		const here: Entry[] = [
			...keyboard.views.map((v) => ({
				label: `Show ${v.label}`,
				keywords: ['view'],
				run: v.set,
				hint: v.key.toUpperCase()
			})),
			...(keyboard.filter ? [{ label: 'Filter runs', run: keyboard.filter, hint: 'F' }] : [])
		];
		if (here.length) out.push(['This page', here]);
		if (org && shell?.projects.length)
			out.push([
				'Projects',
				shell.projects.map((p: ProjectInfo) => ({
					label: p.name,
					keywords: p.description ? [p.description] : [],
					href: projectHref(org, p.name)
				}))
			]);
		if (org && project && groups.length)
			out.push([
				'Groups',
				[
					{ label: 'All groups', href: groupsHref(org, project) },
					...groups.map((g) => ({ label: g.name, href: groupHref(org, project, g.id) }))
				]
			]);
		out.push(['Go to', go]);
		if (keyboard.runs.length)
			out.push(['Runs', keyboard.runs.map((r) => ({ label: r.label, href: r.href }))]);
		out.push([
			'Appearance',
			[
				{ label: 'Light theme', keywords: ['theme'], run: () => setTheme('light') },
				{ label: 'Dark theme', keywords: ['theme'], run: () => setTheme('dark') },
				{ label: 'System theme', keywords: ['theme'], run: () => setTheme('system') }
			]
		]);
		return out;
	});

	function choose(e: Entry) {
		open = false;
		if (e.run) e.run();
		// eslint-disable-next-line svelte/no-navigation-without-resolve -- built with resolve()
		else if (e.href) void goto(e.href);
	}

	const ITEM =
		'flex h-8 cursor-pointer items-center gap-2 rounded-md px-2 text-[13px] outline-none data-selected:bg-muted';
</script>

<!-- Cmd+K: every place and action in one list, filtered as you type. -->
<Dialog.Root bind:open>
	<Dialog.Content
		class="top-[18vh] translate-y-0 gap-0 overflow-hidden p-0 sm:max-w-xl"
		showCloseButton={false}
	>
		<Dialog.Title class="sr-only">Command menu</Dialog.Title>
		<Command.Root loop>
			<Command.Input
				class="h-11 w-full border-b border-divider bg-transparent px-4 text-[14px] outline-none placeholder:text-faint"
				placeholder="Search or jump to…"
				aria-label="Search or jump to"
			/>
			<Command.List class="max-h-[min(60vh,28rem)] overflow-y-auto p-1.5">
				<Command.Viewport>
					<Command.Empty class="px-2 py-6 text-center text-[13px] text-muted-foreground">
						Nothing matches
					</Command.Empty>
					{#each sections as [name, entries] (name)}
						<Command.Group value={name}>
							<Command.GroupHeading
								class="px-2 pt-2 pb-1 text-xs font-medium text-muted-foreground"
							>
								{name}
							</Command.GroupHeading>
							<Command.GroupItems>
								{#each entries as e, i (`${name}/${i}`)}
									<Command.Item
										class={ITEM}
										value={`${e.label} · ${name}`}
										keywords={[e.label, ...(e.keywords ?? [])]}
										onSelect={() => choose(e)}
									>
										<span class="min-w-0 flex-1 truncate">{e.label}</span>
										{#if e.hint}
											<span class="text-[11px] text-faint">{e.hint}</span>
										{/if}
									</Command.Item>
								{/each}
							</Command.GroupItems>
						</Command.Group>
					{/each}
				</Command.Viewport>
			</Command.List>
		</Command.Root>
	</Dialog.Content>
</Dialog.Root>
