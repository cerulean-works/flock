<script lang="ts">
	import ActivityIcon from '@lucide/svelte/icons/activity';
	import BoxIcon from '@lucide/svelte/icons/box';
	import ChartLineIcon from '@lucide/svelte/icons/chart-line';
	import Columns2Icon from '@lucide/svelte/icons/columns-2';
	import DatabaseIcon from '@lucide/svelte/icons/database';
	import FileIcon from '@lucide/svelte/icons/file';
	import FolderIcon from '@lucide/svelte/icons/folder';
	import HardDriveIcon from '@lucide/svelte/icons/hard-drive';
	import LayersIcon from '@lucide/svelte/icons/layers';
	import ListFilterIcon from '@lucide/svelte/icons/list-filter';
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import MonitorIcon from '@lucide/svelte/icons/monitor';
	import MoonIcon from '@lucide/svelte/icons/moon';
	import SettingsIcon from '@lucide/svelte/icons/settings';
	import SunIcon from '@lucide/svelte/icons/sun';
	import TableIcon from '@lucide/svelte/icons/table';
	import UserIcon from '@lucide/svelte/icons/user';
	import { Command, computeCommandScore } from 'bits-ui';
	import type { Component } from 'svelte';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import Keys from '#lib/components/Keys.svelte';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { datasetCall, datasetsApi, datasetsHref } from '#lib/datasets.ts';
	import { groupHref, groupsHref, type GroupList } from '#lib/groups.ts';
	import { keyboard } from '#lib/keys.svelte.ts';
	import { folderApi, modelsHref } from '#lib/models.ts';
	import { bucketApi, nameOf, objectHref, storageHref } from '#lib/storage.ts';
	import { setTheme } from '#lib/theme.svelte.ts';
	import type {
		Bucket,
		DatasetAt,
		DatasetFolder,
		Listing,
		ModelAt,
		ModelFolder,
		ProjectInfo
	} from '#lib/types.ts';
	import { flockFilter } from '#lib/workspace/filters.ts';
	import { entityHref, orgHref, projectHref, runHref } from '#lib/workspace/url.ts';

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
		/** Muted at the row's end: a run's project. */
		detail?: string;
		/** Before the label, 14px and muted; a group shows its colour instead. */
		icon?: Component;
		color?: string;
		/** A place Tab opens here, listing what's in it. */
		level?: () => Level;
	}

	type Sections = [string, Entry[]][];

	/** A place opened in the menu: its chip, and what's in it (searched by Flock when `searched`). */
	interface Level {
		label: string;
		searched?: boolean;
		load: (query: string, signal: AbortSignal) => Promise<Sections>;
	}

	async function getJson<T>(url: string, signal: AbortSignal, init: RequestInit = {}): Promise<T> {
		const res = await fetch(url, { ...init, signal });
		if (!res.ok) throw new Error(`Flock answered ${res.status}`);
		return (await res.json()) as T;
	}

	// ---- The places that open ------------------------------------------------------------

	/** A project: its groups, then its latest runs (Flock searches them as you type). */
	const projectLevel = (o: string, name: string): Level => ({
		label: name,
		searched: true,
		async load(query, signal) {
			const base = `/api/projects/${encodeURIComponent(o)}/${encodeURIComponent(name)}`;
			const [list, runs] = await Promise.all([
				getJson<GroupList>(`${base}/groups`, signal).catch(() => null),
				getJson<{ ids: string[]; data: Record<string, unknown[]> }>(`${base}/runs/query`, signal, {
					method: 'POST',
					headers: { 'content-type': 'application/json' },
					body: JSON.stringify({
						filter: flockFilter(query, []),
						sort: ['-created_at'],
						limit: 50,
						columns: ['display_name']
					})
				})
			]);
			const out: Sections = [];
			if (list?.groups.length)
				out.push([
					'Groups',
					list.groups.map((g) => ({
						label: g.name,
						href: groupHref(o, name, g.id),
						color: g.color
					}))
				]);
			out.push([
				'Runs',
				runs.ids.map((id, i) => {
					const label = String(runs.data.display_name?.[i] ?? id);
					return { label, keywords: [id], href: runHref(o, name, id), icon: ActivityIcon };
				})
			]);
			return out;
		}
	});

	/** A folder of the org's datasets (`path` empty for the top). */
	const datasetLevel = (o: string, path: string): Level => ({
		label: nameOf(path),
		async load(_, signal) {
			const at = await datasetCall<DatasetFolder | DatasetAt>(datasetsApi(o, 'at', { path }), {
				signal
			});
			if (at.kind !== 'folder') return [];
			return [
				['Folders', at.folders.map((f) => datasetFolder(o, f.path))],
				[
					'Datasets',
					at.datasets.map((d) => ({
						label: d.name,
						href: datasetsHref(o, d.path),
						icon: DatabaseIcon
					}))
				]
			];
		}
	});
	const datasetFolder = (o: string, path: string): Entry => ({
		label: nameOf(path),
		keywords: [path],
		href: datasetsHref(o, path),
		icon: FolderIcon,
		level: () => datasetLevel(o, path)
	});

	/** A folder of the org's models (`path` empty for the top). */
	const modelLevel = (o: string, path: string): Level => ({
		label: nameOf(path),
		async load(_, signal) {
			const at = await datasetCall<ModelFolder | ModelAt>(folderApi(o, path), { signal });
			if (at.kind !== 'folder') return [];
			return [
				['Folders', at.folders.map((f) => modelFolder(o, f.path))],
				[
					'Models',
					at.models.map((m) => ({
						label: nameOf(m.name),
						keywords: [m.name],
						href: modelsHref(o, m.name),
						icon: BoxIcon
					}))
				]
			];
		}
	});
	const modelFolder = (o: string, path: string): Entry => ({
		label: nameOf(path),
		keywords: [path],
		href: modelsHref(o, path),
		icon: FolderIcon,
		level: () => modelLevel(o, path)
	});

	/** A folder of a bucket (`prefix` empty for its top, else ending in `/`). */
	const bucketLevel = (o: string, bucket: string, prefix: string): Level => ({
		label: prefix ? nameOf(prefix) : bucket,
		async load(_, signal) {
			const at = await getJson<Listing>(
				`${bucketApi(o, bucket, 'objects')}?${new URLSearchParams({ prefix })}`,
				signal
			);
			return [
				[
					'Folders',
					at.folders.map((key) => ({
						label: nameOf(key),
						keywords: [key],
						href: objectHref(o, bucket, key),
						icon: FolderIcon,
						level: () => bucketLevel(o, bucket, key)
					}))
				],
				[
					'Files',
					at.objects.map((obj) => ({
						label: nameOf(obj.key),
						keywords: [obj.key],
						href: objectHref(o, bucket, obj.key),
						icon: FileIcon
					}))
				]
			];
		}
	});

	// ---- The top level -------------------------------------------------------------------

	// The org's top dataset and model folders and its buckets, read once when the menu first
	// opens (per org), so the top level lists them too.
	let tops = $state<{ org: string; datasets: Entry[]; models: Entry[]; buckets: Entry[] } | null>(
		null
	);
	$effect(() => {
		if (!open || !org || !shell?.s3 || tops?.org === org) return;
		const o = org;
		const controller = new AbortController();
		const { signal } = controller;
		void Promise.all([
			datasetCall<DatasetFolder | DatasetAt>(datasetsApi(o, 'at', { path: '' }), { signal })
				.then((at) => (at.kind === 'folder' ? at.folders.map((f) => datasetFolder(o, f.path)) : []))
				.catch(() => []),
			datasetCall<ModelFolder | ModelAt>(folderApi(o, ''), { signal })
				.then((at) => (at.kind === 'folder' ? at.folders.map((f) => modelFolder(o, f.path)) : []))
				.catch(() => []),
			getJson<{ buckets: Bucket[] }>(`/api/storage/${encodeURIComponent(o)}`, signal)
				.then((b) =>
					b.buckets
						.filter((x) => x.deleted_at === undefined || x.deleted_at === null)
						.map((x): Entry => ({
							label: x.name,
							href: objectHref(o, x.name),
							icon: HardDriveIcon,
							level: () => bucketLevel(o, x.name, '')
						}))
				)
				.catch(() => [])
		]).then(([datasets, models, buckets]) => {
			if (!signal.aborted) tops = { org: o, datasets, models, buckets };
		});
		return () => controller.abort();
	});

	const VIEW_ICONS: Record<string, Component> = {
		t: TableIcon,
		v: Columns2Icon,
		c: ChartLineIcon
	};

	const top = $derived.by((): Sections => {
		const go: Entry[] = [];
		if (org) {
			go.push({ label: 'Runs', href: entityHref(org), hint: 'G R', icon: ActivityIcon });
			if (shell?.s3) {
				go.push({ label: 'Datasets', href: datasetsHref(org), hint: 'G D', icon: DatabaseIcon });
				go.push({ label: 'Models', href: modelsHref(org), hint: 'G M', icon: BoxIcon });
				go.push({ label: 'Storage', href: storageHref(org), hint: 'G S', icon: HardDriveIcon });
			}
			go.push({ label: 'Settings', href: orgHref(org), hint: 'G ,', icon: SettingsIcon });
		}
		go.push({ label: 'Account and keys', href: resolve('/(app)/account'), icon: UserIcon });

		const out: Sections = [];
		const here: Entry[] = [
			...keyboard.views.map((v) => ({
				label: `Show ${v.label}`,
				keywords: ['view'],
				run: v.set,
				hint: v.key.toUpperCase(),
				icon: VIEW_ICONS[v.key]
			})),
			...(keyboard.filter
				? [{ label: 'Filter runs', run: keyboard.filter, hint: 'F', icon: ListFilterIcon }]
				: [])
		];
		if (here.length) out.push(['This page', here]);
		if (org && shell?.projects.length) {
			const o = org;
			out.push([
				'Projects',
				shell.projects.map((p: ProjectInfo) => ({
					label: p.name,
					keywords: p.description ? [p.description] : [],
					href: projectHref(o, p.name),
					icon: FolderIcon,
					level: () => projectLevel(o, p.name)
				}))
			]);
		}
		if (org && project && groups.length)
			out.push([
				'Groups',
				[
					{ label: 'All groups', href: groupsHref(org, project), icon: LayersIcon },
					...groups.map((g) => ({
						label: g.name,
						href: groupHref(org, project, g.id),
						color: g.color
					}))
				]
			]);
		out.push(['Go to', go]);
		if (keyboard.runs.length)
			out.push([
				'Runs',
				keyboard.runs.map((r) => ({
					label: r.label,
					href: r.href,
					keywords: [r.project],
					detail: r.project,
					icon: ActivityIcon
				}))
			]);
		const read = tops;
		if (read && read.org === org) {
			if (read.datasets.length) out.push(['Dataset folders', read.datasets]);
			if (read.models.length) out.push(['Model folders', read.models]);
			if (read.buckets.length) out.push(['Buckets', read.buckets]);
		}
		out.push([
			'Appearance',
			[
				{ label: 'Light theme', keywords: ['theme'], run: () => setTheme('light'), icon: SunIcon },
				{ label: 'Dark theme', keywords: ['theme'], run: () => setTheme('dark'), icon: MoonIcon },
				{
					label: 'System theme',
					keywords: ['theme'],
					run: () => setTheme('system'),
					icon: MonitorIcon
				}
			]
		]);
		return out;
	});

	// ---- Levels opened -------------------------------------------------------------------

	let query = $state('');
	/** The highlighted row's value (`section/index`). */
	let active = $state('');
	let stack = $state<Level[]>([]);
	let inside = $state<Sections>([]);
	let loading = $state(false);
	let failed = $state(false);

	const level = $derived(stack.at(-1) ?? null);
	const sections = $derived(level ? inside : top);
	/** A row's value, unique in the list; filtering reads its keywords. */
	const valueOf = (section: string, i: number) => `${section}/${i}`;
	const activeEntry = $derived.by(() => {
		const at = active.lastIndexOf('/');
		if (at < 0) return null;
		const [, entries] = sections.find(([name]) => name === active.slice(0, at)) ?? [];
		return entries?.[Number(active.slice(at + 1))] ?? null;
	});

	// What's in the level open: read again as the search changes where Flock searches it.
	const levelQuery = $derived(level?.searched ? query.trim() : '');
	$effect(() => {
		const l = level;
		if (!l) return;
		const q = levelQuery;
		const controller = new AbortController();
		loading = true;
		failed = false;
		const timer = setTimeout(
			() => {
				l.load(q, controller.signal)
					.then((s) => {
						if (!controller.signal.aborted) inside = s.filter(([, e]) => e.length);
					})
					.catch(() => {
						if (!controller.signal.aborted) {
							inside = [];
							failed = true;
						}
					})
					.finally(() => {
						if (!controller.signal.aborted) loading = false;
					});
			},
			q ? 200 : 0
		);
		return () => {
			clearTimeout(timer);
			controller.abort();
		};
	});

	function enter(e: Entry) {
		if (!e.level) return;
		inside = [];
		stack = [...stack, e.level()];
		query = '';
	}
	function up() {
		inside = [];
		stack = stack.slice(0, -1);
		query = '';
	}

	// Closed, the menu starts again at the top.
	$effect(() => {
		if (open) return;
		stack = [];
		inside = [];
		query = '';
	});

	function onkeydown(e: KeyboardEvent) {
		const field = e.currentTarget as HTMLInputElement;
		const atEnd = field.selectionStart === field.value.length;
		if ((e.key === 'Tab' && !e.shiftKey) || (e.key === 'ArrowRight' && atEnd)) {
			if (activeEntry?.level) {
				e.preventDefault();
				enter(activeEntry);
			} else if (e.key === 'Tab') e.preventDefault();
		} else if (e.key === 'Backspace' && !query && stack.length) {
			e.preventDefault();
			up();
		}
	}

	/** Escape clears the search, then goes up a level, then closes. */
	function onEscapeKeydown(e: KeyboardEvent) {
		if (query) {
			e.preventDefault();
			query = '';
		} else if (stack.length) {
			e.preventDefault();
			up();
		}
	}

	function choose(e: Entry) {
		open = false;
		if (e.run) e.run();
		// eslint-disable-next-line svelte/no-navigation-without-resolve -- built with resolve()
		else if (e.href) void goto(e.href);
	}

	/** Matches on the label and the row's own words, not on its value. */
	const filter = (_: string, search: string, keywords?: string[]) =>
		computeCommandScore(keywords?.[0] ?? '', search, keywords?.slice(1));

	const ITEM =
		'flex h-8 cursor-pointer items-center gap-2 rounded-md px-2 text-[13px] outline-none data-selected:bg-muted';
	const showKeys = $derived(stack.length > 0 || !!activeEntry?.level);
</script>

<!-- Cmd+K: every place and action in one list, filtered as you type. Tab opens a project,
     folder or bucket in place; Enter goes to the row's page. -->
<Dialog.Root bind:open>
	<Dialog.Content
		class="top-[18vh] translate-y-0 gap-0 overflow-hidden p-0 sm:max-w-xl"
		showCloseButton={false}
		{onEscapeKeydown}
	>
		<Dialog.Title class="sr-only">Command menu</Dialog.Title>
		<Command.Root loop class="min-w-0" bind:value={active} {filter}>
			<div class="flex h-11 min-w-0 items-center gap-1.5 border-b border-divider px-3.5">
				{#each stack as l, i (i)}
					<span
						class="max-w-40 flex-none truncate rounded bg-muted px-1.5 py-0.5 text-[12px] text-muted-foreground"
						>{l.label} ›</span
					>
				{/each}
				<Command.Input
					class="h-full min-w-0 flex-1 bg-transparent text-[14px] outline-none placeholder:text-faint"
					placeholder={level ? `Search ${level.label}…` : 'Search or jump to…'}
					aria-label={level ? `Search ${level.label}` : 'Search or jump to'}
					bind:value={query}
					{onkeydown}
				/>
				{#if loading}
					<LoaderCircleIcon class="size-3.5 flex-none animate-spin text-muted-foreground" />
				{/if}
			</div>
			<Command.List class="max-h-[min(60vh,28rem)] overflow-y-auto p-1.5">
				<Command.Viewport>
					{#if !loading}
						<Command.Empty class="px-2 py-6 text-center text-[13px] text-muted-foreground">
							{failed ? 'Couldn’t load this' : level ? 'Nothing here matches' : 'Nothing matches'}
						</Command.Empty>
					{/if}
					{#each sections as [name, entries] (name)}
						<Command.Group value={name}>
							<Command.GroupHeading
								class="px-2 pt-2 pb-1 text-xs font-medium text-muted-foreground"
							>
								{name}
							</Command.GroupHeading>
							<Command.GroupItems>
								{#each entries as e, i (valueOf(name, i))}
									<Command.Item
										class={ITEM}
										value={valueOf(name, i)}
										keywords={[e.label, ...(e.keywords ?? [])]}
										onSelect={() => choose(e)}
									>
										<!-- The icon's column starts where the headings' text does. -->
										<span
											class="flex size-3.5 flex-none items-center justify-center"
											aria-hidden="true"
										>
											{#if e.color}
												<span class="size-2 rounded-[2px]" style:background={e.color}></span>
											{:else if e.icon}
												<e.icon class="size-3.5 text-muted-foreground" />
											{/if}
										</span>
										<span class="min-w-0 flex-1 truncate">{e.label}</span>
										{#if e.detail}
											<span class="max-w-40 min-w-0 truncate text-muted-foreground">{e.detail}</span
											>
										{/if}
										{#if e.hint}
											<Keys keys={e.hint} />
										{/if}
										{#if e.level}
											<Keys keys="Tab" />
										{/if}
									</Command.Item>
								{/each}
							</Command.GroupItems>
						</Command.Group>
					{/each}
				</Command.Viewport>
			</Command.List>
			{#if showKeys}
				<p
					class="flex items-center gap-1.5 border-t border-divider px-3.5 py-1.5 text-[11px] text-muted-foreground"
				>
					<Keys keys="↵" /> go <span aria-hidden="true">·</span>
					<Keys keys="Tab" /> open <span aria-hidden="true">·</span>
					<Keys keys="⌫" /> up
				</p>
			{/if}
		</Command.Root>
	</Dialog.Content>
</Dialog.Root>
