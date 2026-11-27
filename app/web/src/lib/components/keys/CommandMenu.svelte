<script lang="ts">
	import ActivityIcon from '@lucide/svelte/icons/activity';
	import BoxIcon from '@lucide/svelte/icons/box';
	import ChartLineIcon from '@lucide/svelte/icons/chart-line';
	import Columns2Icon from '@lucide/svelte/icons/columns-2';
	import DatabaseIcon from '@lucide/svelte/icons/database';
	import FileIcon from '@lucide/svelte/icons/file';
	import FileTextIcon from '@lucide/svelte/icons/file-text';
	import FolderIcon from '@lucide/svelte/icons/folder';
	import GitCommitIcon from '@lucide/svelte/icons/git-commit-horizontal';
	import HardDriveIcon from '@lucide/svelte/icons/hard-drive';
	import InfoIcon from '@lucide/svelte/icons/info';
	import LayersIcon from '@lucide/svelte/icons/layers';
	import ListChecksIcon from '@lucide/svelte/icons/list-checks';
	import ListFilterIcon from '@lucide/svelte/icons/list-filter';
	import ListTreeIcon from '@lucide/svelte/icons/list-tree';
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import MonitorIcon from '@lucide/svelte/icons/monitor';
	import MoonIcon from '@lucide/svelte/icons/moon';
	import ScrollTextIcon from '@lucide/svelte/icons/scroll-text';
	import SettingsIcon from '@lucide/svelte/icons/settings';
	import SunIcon from '@lucide/svelte/icons/sun';
	import TableIcon from '@lucide/svelte/icons/table';
	import UserIcon from '@lucide/svelte/icons/user';
	import { Command, computeCommandScore } from 'bits-ui';
	import type { Component, Snippet } from 'svelte';
	import SearchIcon from '@lucide/svelte/icons/search';
	import HouseIcon from '@lucide/svelte/icons/house';
	import PinIcon from '@lucide/svelte/icons/pin';
	import { pins, mainMetric } from '#lib/home/pins.svelte.ts';
	import { recent, type Visit } from '#lib/recent.svelte.ts';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import Keys from '#lib/components/Keys.svelte';
	import { datasetCall, datasetsApi, datasetsHref } from '#lib/datasets.ts';
	import { groupHref, groupsHref, type GroupList } from '#lib/groups.ts';
	import { keyboard } from '#lib/keys.svelte.ts';
	import { folderApi, modelApi, modelCall, modelsHref } from '#lib/models.ts';
	import { bucketApi, nameOf, objectHref, storageHref } from '#lib/storage.ts';
	import { setTheme } from '#lib/theme.svelte.ts';
	import type {
		Bucket,
		DatasetAt,
		DatasetFolder,
		Listing,
		ModelAt,
		ModelFolder,
		ModelVersionsPage,
		ProjectInfo,
		VersionsPage
	} from '#lib/types.ts';
	import { flockFilter } from '#lib/workspace/filters.ts';
	import { entityHref, orgHref, projectHref, runHref } from '#lib/workspace/url.ts';

	interface Props {
		/** In use: the dialog is open, or Home's field has been used. Closed, it starts over. */
		open?: boolean;
		/**
		 * `dialog`: Cmd+K's list, always shown. `inline`: Home's field (H2), its results shown in
		 * place under it only once there's something typed or Down is pressed.
		 */
		variant?: 'dialog' | 'inline';
		/** A row was chosen, or Escape had nothing left to undo. */
		onclose?: () => void;
		/** Inline: told whether the results show, so the page can step back. */
		onshowing?: (showing: boolean) => void;
		/** Inline: what sits on the field's top edge (Home's status tab). */
		above?: Snippet;
	}

	let { open = $bindable(false), variant = 'dialog', onclose, onshowing, above }: Props = $props();
	const inline = $derived(variant === 'inline');

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
		/** A run's state: its dot takes the icon's place, as in the runs tables. */
		state?: string;
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
						columns: ['display_name', 'job_type', 'state']
					})
				})
			]);
			const out: Sections = [];
			// Flock searches the runs; the groups are matched here, by name.
			const groups = (list?.groups ?? []).filter((g) =>
				g.name.toLowerCase().includes(query.trim().toLowerCase())
			);
			if (groups.length)
				out.push([
					'Groups',
					groups.map((g) => ({
						label: g.name,
						href: groupHref(o, name, g.id),
						color: g.color
					}))
				]);
			out.push([
				'Runs',
				runs.ids.map((id, i) => {
					const label = String(runs.data.display_name?.[i] ?? id);
					const job = (runs.data.job_type?.[i] ?? null) as string | null;
					const state = (runs.data.state?.[i] ?? undefined) as string | undefined;
					return { ...runEntry(o, name, id, label, job), state };
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
						icon: DatabaseIcon,
						level: () => datasetItemLevel(o, d.path, d.version)
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
						icon: BoxIcon,
						level: () => modelItemLevel(o, m.name, m.primary)
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

	/** A page of an item at one of its tabs. */
	const tabHref = (href: string, tab: string) => `${href}?tab=${tab}`;

	/**
	 * A run: its tabs. An eval run's open on Results and add Compare with…; a run listed without
	 * its job type (the page's runs) is asked for it when opened.
	 */
	const runLevel = (
		o: string,
		project: string,
		id: string,
		label: string,
		job?: string | null
	): Level => ({
		label,
		async load(_, signal) {
			let kind = job;
			if (kind === undefined) {
				const base = `/api/projects/${encodeURIComponent(o)}/${encodeURIComponent(project)}`;
				const one = await getJson<{ data: Record<string, unknown[]> }>(
					`${base}/runs/query`,
					signal,
					{
						method: 'POST',
						headers: { 'content-type': 'application/json' },
						body: JSON.stringify({ filter: { name: id }, limit: 1, columns: ['job_type'] })
					}
				).catch(() => null);
				kind = (one?.data.job_type?.[0] ?? null) as string | null;
			}
			const href = runHref(o, project, id);
			const evals = kind === 'eval';
			const pages: Entry[] = [
				...(evals ? [{ label: 'Results', href, icon: ListChecksIcon }] : []),
				{ label: 'Charts', href: tabHref(href, 'charts'), icon: ChartLineIcon },
				{ label: 'Logs', href: tabHref(href, 'logs'), icon: ScrollTextIcon },
				{ label: 'Files', href: tabHref(href, 'files'), icon: FileIcon },
				...(evals ? [{ label: 'Compare with…', href: `${href}/compare`, icon: Columns2Icon }] : [])
			];
			return [['Pages', pages]];
		}
	});
	const runEntry = (
		o: string,
		project: string,
		id: string,
		label: string,
		job?: string | null,
		detail?: string
	): Entry => ({
		label,
		keywords: detail ? [id, detail] : [id],
		href: runHref(o, project, id),
		detail,
		icon: ActivityIcon,
		level: () => runLevel(o, project, id, label, job)
	});

	/** A dataset: its tabs, then its versions (the newest page of them). */
	const datasetItemLevel = (o: string, path: string, primary: number | null): Level => ({
		label: nameOf(path),
		async load(_, signal) {
			const href = datasetsHref(o, path);
			const list = await datasetCall<VersionsPage>(datasetsApi(o, 'versions', { path }), {
				signal
			}).catch(() => null);
			const out: Sections = [
				[
					'Pages',
					[
						{ label: 'About', href, icon: InfoIcon },
						{ label: 'Viewer', href: tabHref(href, 'rows'), icon: TableIcon },
						{ label: 'Schema', href: tabHref(href, 'schema'), icon: ListTreeIcon },
						{ label: 'Versions', href: tabHref(href, 'versions'), icon: GitCommitIcon }
					]
				]
			];
			if (list?.versions.length)
				out.push([
					'Versions',
					list.versions.map((v) => ({
						label: `v${v.version}`,
						keywords: v.tags,
						detail: v.version === primary ? 'Primary' : v.tags.join(', ') || undefined,
						href: `${href}?version=${v.version}`,
						icon: GitCommitIcon
					}))
				]);
			return out;
		}
	});

	/** A model: its tabs, then its versions (the newest page of them). */
	const modelItemLevel = (o: string, name: string, primary: number | null): Level => ({
		label: nameOf(name),
		async load(_, signal) {
			const href = modelsHref(o, name);
			const list = await modelCall<ModelVersionsPage>(modelApi(o, name, 'versions'), {
				signal
			}).catch(() => null);
			const out: Sections = [
				[
					'Pages',
					[
						{ label: 'Overview', href, icon: FileTextIcon },
						{ label: 'Versions', href: tabHref(href, 'versions'), icon: GitCommitIcon },
						{ label: 'Evals', href: tabHref(href, 'evals'), icon: ListChecksIcon },
						{ label: 'Files', href: tabHref(href, 'files'), icon: FileIcon }
					]
				]
			];
			if (list?.versions.length)
				out.push([
					'Versions',
					list.versions.map((v) => ({
						label: `v${v.number}`,
						keywords: v.tags,
						detail: v.number === primary ? 'Primary' : v.tags.join(', ') || undefined,
						href: `${href}?version=${v.number}`,
						icon: GitCommitIcon
					}))
				]);
			return out;
		}
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

	const VISIT_ICONS: Record<Visit['kind'], Component> = {
		run: ActivityIcon,
		project: FolderIcon,
		group: LayersIcon,
		dataset: DatabaseIcon,
		model: BoxIcon,
		storage: HardDriveIcon
	};

	const VIEW_ICONS: Record<string, Component> = {
		t: TableIcon,
		v: Columns2Icon,
		c: ChartLineIcon
	};

	const top = $derived.by((): Sections => {
		const go: Entry[] = [];
		if (org) {
			go.push({ label: 'Home', href: resolve('/(app)'), hint: 'G H', icon: HouseIcon });
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
		// On a run's page: pin one of its curves to Home (H10), its loss first.
		const run = page.data.run as
			{ id: string; entity: string; project: string; display_name: string | null } | undefined;
		const history = page.data.history as { series: Record<string, unknown> } | undefined;
		if (org && run && history && run.entity === org) {
			const keys = Object.keys(history.series).filter((k) => !k.startsWith('_'));
			const first = mainMetric(keys);
			const ordered = first ? [first, ...keys.filter((k) => k !== first)] : keys;
			if (ordered.length)
				here.push({
					label: 'Pin a metric to Home',
					keywords: ['pin', 'plot', 'chart'],
					icon: PinIcon,
					level: () => ({
						label: 'Pin',
						async load() {
							const label = run.display_name || run.id;
							return [
								[
									'Metrics',
									ordered.map((metric): Entry => {
										const pin = { org, project: run.project, run: run.id, label, metric };
										return {
											label: metric,
											detail: pins.has(pin) ? 'Pinned' : undefined,
											icon: PinIcon,
											run: () => pins.add(pin)
										};
									})
								]
							];
						}
					})
				});
		}
		// What was opened lately leads (Harry: recent ranked higher), then this page's own
		// commands, then the fixed places.
		const visits = recent.all.filter((v) => v.href !== page.url.pathname).slice(0, 5);
		if (visits.length)
			out.push([
				'Recent',
				visits.map((v): Entry => ({
					label: v.label,
					href: v.href,
					detail: v.detail,
					keywords: v.detail ? [v.detail] : [],
					icon: VISIT_ICONS[v.kind]
				}))
			]);
		if (here.length) out.push(['This page', here]);
		out.push(['Go to', go]);
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
		// The lists asked for most, as commands (H11).
		if (org) {
			const runs = entityHref(org);
			out.push([
				'Views',
				[
					{
						label: 'My runs',
						keywords: ['mine', 'show'],
						href: `${runs}?owner=me`,
						icon: UserIcon
					},
					{
						label: 'My failed runs',
						keywords: ['mine', 'crashed', 'show'],
						href: `${runs}?owner=me&state=failed`,
						icon: ActivityIcon
					},
					{
						label: 'Failed runs',
						keywords: ['crashed', 'show', 'errors'],
						href: `${runs}?state=failed`,
						icon: ActivityIcon
					}
				]
			]);
		}
		if (keyboard.runs.length)
			out.push([
				'Runs',
				keyboard.runs.map((r) => {
					const id = r.href.slice(r.href.lastIndexOf('/') + 1);
					return org
						? runEntry(org, r.project, id, r.label, undefined, r.project)
						: {
								label: r.label,
								href: r.href,
								keywords: [r.project],
								detail: r.project,
								icon: ActivityIcon
							};
				})
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
	// A level Flock searches is matched here as well, by the same score, so rows from the last
	// answer narrow at once while the next one is on its way (Enter never takes a stale row).
	// While the inline list closes it keeps the rows it last showed: clearing the peek would
	// otherwise swap in the whole list for the frames the height takes to fold (a tall snap).
	let lastShown: Sections = [];
	const sections = $derived.by((): Sections => {
		if (inline && !visible) return lastShown;
		lastShown = shownSections();
		return lastShown;
	});
	function shownSections(): Sections {
		if (!level) return peeking ? top.slice(0, 1) : top;
		const q = query.trim();
		if (!level.searched || !q) return inside;
		return inside
			.map(([name, entries]): [string, Entry[]] => [
				name,
				entries.filter((e) => filter('', q, [e.label, ...(e.keywords ?? [])]) > 0)
			])
			.filter(([, entries]) => entries.length);
	}
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
		cameFrom = stack.at(-1)?.label ?? null;
		inside = [];
		stack = stack.slice(0, -1);
		query = '';
	}
	// Back up a level, the highlight lands on the row just left (not the list's first row, which
	// is Recent's or Go to's now).
	let cameFrom = $state<string | null>(null);
	$effect(() => {
		const from = cameFrom;
		if (from == null || level) return;
		for (const [name, entries] of sections) {
			const i = entries.findIndex((e) => e.level && e.label === from);
			if (i >= 0) {
				// After the list has settled: it selects its first row on a change of rows.
				const value = valueOf(name, i);
				requestAnimationFrame(() => (active = value));
				break;
			}
		}
		cameFrom = null;
	});

	// Closed, the menu starts again at the top.
	$effect(() => {
		if (open) return;
		reset();
	});
	function reset() {
		stack = [];
		inside = [];
		query = '';
		expanded = false;
		peek = false;
	}

	/**
	 * Inline, two steps (Harry): focusing the field peeks at the first section (Recent, else Go
	 * to); typing or Down shows everything.
	 */
	let expanded = $state(false);
	let peek = $state(false);
	// Derived straight from the state that opens it, so it closes in the same update that
	// clears that state; the page is told after.
	const visible = $derived(inline ? !!query || stack.length > 0 || expanded || peek : open);
	$effect(() => {
		onshowing?.(visible);
	});
	const peeking = $derived(inline && peek && !expanded && !query && !stack.length);

	function onkeydown(e: KeyboardEvent) {
		const field = e.currentTarget as HTMLInputElement;
		const atEnd = field.selectionStart === field.value.length;
		if ((e.key === 'Tab' && !e.shiftKey) || (e.key === 'ArrowRight' && atEnd)) {
			if (!visible) return;
			if (activeEntry?.level) {
				e.preventDefault();
				enter(activeEntry);
			} else if (e.key === 'Tab') e.preventDefault();
		} else if (e.key === 'Backspace' && !query && stack.length) {
			e.preventDefault();
			up();
		} else if (inline && e.key === 'ArrowDown' && (!visible || peeking)) {
			// From a peek, Down first opens the whole list; the cursor keeps its row.
			if (!visible) e.preventDefault();
			expanded = true;
		} else if (inline && e.key === 'Escape') {
			e.preventDefault();
			if (!escape()) {
				field.blur();
				onclose?.();
			}
		}
	}

	/** Escape clears the search, then goes up a level, then folds the list; false when nothing did. */
	export function escape(): boolean {
		if (query) query = '';
		else if (stack.length) up();
		else if (inline && (expanded || peek)) {
			expanded = false;
			peek = false;
		} else return false;
		return true;
	}

	function choose(e: Entry) {
		if (inline) reset();
		else open = false;
		onclose?.();
		if (e.run) e.run();
		// eslint-disable-next-line svelte/no-navigation-without-resolve -- built with resolve()
		else if (e.href) void goto(e.href);
	}

	/** Matches on the label and the row's own words, not on its value. */
	const filter = (value: string, search: string, keywords?: string[]) => {
		const score = computeCommandScore(keywords?.[0] ?? '', search, keywords?.slice(1));
		// Recent pages rank above an equal match elsewhere.
		return value.startsWith('Recent/') ? Math.min(1, score * 1.25) : score;
	};

	// Static class strings per state so Tailwind sees every class (as StateBadge).
	const DOT: Record<string, string> = {
		running: 'bg-state-running',
		preempting: 'bg-state-preempting',
		finished: 'bg-state-finished',
		crashed: 'bg-state-failed',
		failed: 'bg-state-failed'
	};

	const ITEM =
		'flex h-8 cursor-pointer items-center gap-2 rounded-md px-2 text-[13px] outline-none data-selected:bg-muted';
	const showKeys = $derived(stack.length > 0 || !!activeEntry?.level);
	let input = $state<HTMLInputElement | null>(null);
	/** Inline: Home puts the cursor here on arrival (quietly), Cmd+K with a peek at Recent. */
	let quiet = false;
	export function focus(peekIt = false) {
		quiet = !peekIt;
		input?.focus();
		quiet = false;
		if (peekIt) peek = true;
	}
	function onfocus() {
		if (inline && !quiet) peek = true;
	}
	/** Leaving the field folds the list, unless focus moved into it. */
	function onfocusout(e: FocusEvent) {
		const root = (e.currentTarget as HTMLElement).closest('[data-command-surface]');
		if (root?.contains(e.relatedTarget as Node | null)) return;
		peek = false;
		expanded = false;
	}
</script>

<!-- Every place and action in one list, filtered as you type. Tab opens a project, folder or
     bucket in place; Enter goes to the row's page. Cmd+K shows it in a dialog; Home inline. -->
{#snippet chips()}
	{#each stack as l, i (i)}
		<span
			class="max-w-40 flex-none truncate rounded bg-muted px-1.5 py-0.5 text-[12px] text-muted-foreground"
			>{l.label} ›</span
		>
	{/each}
{/snippet}

{#snippet field(cls: string)}
	<Command.Input
		bind:ref={input}
		class={cls}
		placeholder={level ? `Search ${level.label}…` : 'Search or jump to…'}
		aria-label={level ? `Search ${level.label}` : 'Search or jump to'}
		bind:value={query}
		{onkeydown}
		{onfocus}
		onblur={inline ? onfocusout : undefined}
	/>
	{#if loading}
		<LoaderCircleIcon class="size-3.5 flex-none animate-spin text-muted-foreground" />
	{/if}
{/snippet}

{#snippet list()}
	<Command.List class="max-h-[min(60vh,28rem)] overflow-y-auto p-1.5">
		<Command.Viewport>
			{#if !loading}
				<Command.Empty class="px-2 py-6 text-center text-[13px] text-muted-foreground">
					{failed ? 'Couldn’t load this' : level ? 'Nothing here matches' : 'Nothing matches'}
				</Command.Empty>
			{/if}
			{#each sections as [name, entries] (name)}
				<Command.Group value={name}>
					<Command.GroupHeading class="px-2 pt-2 pb-1 text-xs font-medium text-muted-foreground">
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
									{#if e.state}
										<span class={['size-1.5 rounded-full', DOT[e.state] ?? 'bg-faint']}></span>
									{:else if e.color}
										<span class="size-2 rounded-[2px]" style:background={e.color}></span>
									{:else if e.icon}
										<e.icon class="size-3.5 text-muted-foreground" />
									{/if}
								</span>
								<span class="min-w-0 flex-1 truncate">{e.label}</span>
								{#if e.detail}
									<span class="max-w-40 min-w-0 truncate text-muted-foreground">{e.detail}</span>
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
{/snippet}

<!-- A level Flock searches comes back filtered: matching it again here hides rows. -->
<Command.Root loop class="min-w-0" bind:value={active} {filter} shouldFilter={!level?.searched}>
	{#if inline}
		<!-- One surface: the field, and under it the results, which open downward (H2). The
		     surface floats over the page, so nothing below moves. -->
		<div class="relative h-15">
			{@render above?.()}
			<!-- A press in the list keeps the field's focus, so the click lands before it folds. -->
			<div
				data-command-surface
				onpointerdown={(e) => {
					if (e.target instanceof Element && !e.target.closest('input')) e.preventDefault();
				}}
				role="presentation"
				class={[
					'absolute inset-x-0 top-0 z-10 overflow-hidden rounded-2xl bg-background ring-1 transition-[box-shadow,--tw-ring-color] duration-300 dark:bg-popover',
					visible
						? 'shadow-[0_12px_40px_-8px_rgb(0_0_0/0.18)] ring-foreground/15'
						: 'shadow-[0_1px_2px_rgb(0_0_0/0.04),0_4px_16px_-4px_rgb(0_0_0/0.08)] ring-foreground/12 focus-within:shadow-[0_0_0_3px_color-mix(in_oklab,var(--color-link)_12%,transparent),0_0_32px_-4px_color-mix(in_oklab,var(--color-link)_30%,transparent)] focus-within:ring-link/30'
				]}
			>
				<label class="flex h-15 min-w-0 cursor-text items-center gap-3 pr-4 pl-5">
					<SearchIcon class="size-[18px] flex-none text-muted-foreground" aria-hidden="true" />
					{@render chips()}
					{@render field(
						'h-full min-w-0 flex-1 bg-transparent text-[16px] outline-none placeholder:text-faint'
					)}
					<Keys keys="⌘ K" class="max-sm:hidden" />
				</label>
				<div
					class={[
						'expand grid transition-[grid-template-rows] duration-250 ease-[cubic-bezier(0.2,0.8,0.25,1)]',
						visible ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'
					]}
					inert={!visible}
				>
					<!-- The rows fade in just behind the height, and out ahead of it, so none is cut
					     mid-line by the edge as it moves. -->
					<div class="min-h-0 overflow-hidden">
						<div
							class={[
								'border-t border-divider transition-[opacity,translate] ease-out',
								visible
									? 'translate-y-0 opacity-100 delay-100 duration-200'
									: '-translate-y-1 opacity-0 duration-100'
							]}
						>
							{@render list()}
						</div>
					</div>
				</div>
			</div>
		</div>
	{:else}
		<div class="flex h-11 min-w-0 items-center gap-1.5 border-b border-divider px-3.5">
			{@render chips()}
			{@render field(
				'h-full min-w-0 flex-1 bg-transparent text-[14px] outline-none placeholder:text-faint'
			)}
		</div>
		{@render list()}
	{/if}
</Command.Root>
