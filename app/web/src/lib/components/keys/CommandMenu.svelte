<script lang="ts">
	import ActivityIcon from '@lucide/svelte/icons/activity';
	import ArrowDownIcon from '@lucide/svelte/icons/arrow-down';
	import ArrowUpDownIcon from '@lucide/svelte/icons/arrow-up-down';
	import ArrowUpIcon from '@lucide/svelte/icons/arrow-up';
	import BracesIcon from '@lucide/svelte/icons/braces';
	import CalendarIcon from '@lucide/svelte/icons/calendar';
	import ArrowLeftRightIcon from '@lucide/svelte/icons/arrow-left-right';
	import CheckIcon from '@lucide/svelte/icons/check';
	import Rows2Icon from '@lucide/svelte/icons/rows-2';
	import GitCompareArrowsIcon from '@lucide/svelte/icons/git-compare-arrows';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import TagIcon from '@lucide/svelte/icons/tag';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import CircleDotIcon from '@lucide/svelte/icons/circle-dot';
	import HashIcon from '@lucide/svelte/icons/hash';
	import TimerIcon from '@lucide/svelte/icons/timer';
	import ToggleLeftIcon from '@lucide/svelte/icons/toggle-left';
	import TypeIcon from '@lucide/svelte/icons/type';
	import XIcon from '@lucide/svelte/icons/x';
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
	import { Command } from 'bits-ui';
	import { cubicOut } from 'svelte/easing';
	import { fly } from 'svelte/transition';
	import { searchScore } from '#lib/search.ts';
	import type { Component, Snippet } from 'svelte';
	import SearchIcon from '@lucide/svelte/icons/search';
	import HouseIcon from '@lucide/svelte/icons/house';
	import PinIcon from '@lucide/svelte/icons/pin';
	import { pins, mainMetric } from '#lib/home/pins.svelte.ts';
	import { pathOf, recent, type Visit } from '#lib/recent.svelte.ts';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import Keys from '#lib/components/Keys.svelte';
	import { datasetCall, datasetsApi, datasetsHref } from '#lib/datasets.ts';
	import { groupHref, groupsHref, type GroupList } from '#lib/groups.ts';
	import { keyboard, type PaletteColumn, type PaletteCompare } from '#lib/keys.svelte.ts';
	import { compareHref } from '#lib/evals.ts';
	import type { ColumnFilterProps } from '#lib/workspace/column-filter.ts';
	import type { FilterValue } from '#lib/components/ColumnFilter.svelte';
	import { naturalOrder, orderWords } from '#lib/workspace/natural-order.ts';
	import { parseWhen } from '#lib/workspace/when.ts';
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
		ItemSuites,
		ProjectInfo,
		SortOrder,
		VersionsPage
	} from '#lib/types.ts';
	import {
		chipSegments,
		dropChip,
		flockFilter,
		joinSegments,
		type RunFilter
	} from '#lib/workspace/filters.ts';
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
		/** Open inside Filter or Sort (F, Shift+S); cleared once it has. */
		start?: 'filter' | 'sort' | null;
	}

	let {
		open = $bindable(false),
		variant = 'dialog',
		onclose,
		onshowing,
		above,
		start = $bindable(null)
	}: Props = $props();
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
		/** A place Tab opens here, listing what's in it. Enter opens it too when the row has
		 *  nowhere to go and nothing to run. */
		level?: () => Level;
		/** A value in a checklist: ticked or not, and what ticking it does. */
		checked?: boolean;
		toggle?: () => void;
		/** A value's dot colour class (a run state's). */
		dot?: string;
		/** Running it leaves the menu open (a clear, a switch): more may follow. */
		stay?: boolean;
		/** ⌃X on the row: takes away what it holds (a filter in use, a tag). */
		remove?: () => void;
		/** What ⌃X is called in the bar ("Remove" unless said). */
		removeLabel?: string;
		/** Shown in the destructive colour: a delete waiting for its second ⌃X. */
		danger?: boolean;
		/** Makes what was typed (a tag, a group): Enter's row when nothing else matches. */
		create?: boolean;
	}

	type Sections = [string, Entry[]][];

	/** A place opened in the menu: its chip, and what's in it (searched by Flock when `searched`). */
	interface Level {
		label: string;
		searched?: boolean;
		load?: (query: string, signal: AbortSignal) => Promise<Sections>;
		/** Rows read live (a filter's values follow each tick), instead of loaded once. */
		rows?: () => Sections;
		/** Its rows are values to tick: Enter, Space or Tab ticks; Escape is done. */
		checklist?: boolean;
		/** ⌃X anywhere in it: clears what it sets (a column's filter). */
		clear?: () => void;
		/** Called when come back to from a level inside: reads again what it froze. */
		refresh?: () => void;
		/** The field takes a value instead of searching: Tab flips the way, Enter applies. */
		input?: {
			ops: string[];
			placeholder: string;
			/** False when the text can't be applied (a date that reads as none). */
			apply: (text: string, op: number) => boolean;
		};
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

	// ---- Filter and sort (the page's, as its headers edit them) ---------------------------

	/** "Failed, Crashed", "≥ 0.001", "contains adam": what a column filters to now. */
	function summary(p: ColumnFilterProps): string | undefined {
		if (!p.active) return undefined;
		if (p.kind === 'range') {
			const parts = [];
			if (p.range?.min) parts.push(`${p.date ? 'from' : '≥'} ${p.range.min.replace('T', ' ')}`);
			if (p.range?.max) parts.push(`${p.date ? 'to' : '≤'} ${p.range.max.replace('T', ' ')}`);
			return parts.join(', ');
		}
		if (p.kind === 'text') return `contains ${p.text}`;
		const names = (p.selected ?? []).map((v) => p.values?.find((o) => o.value === v)?.label ?? v);
		return names.length > 2
			? `${names.slice(0, 2).join(', ')} +${names.length - 2}`
			: names.join(', ');
	}

	/** Filter: the columns, the ones in use first, each opening its values or its input. */
	/** The column a chip filters. */
	const chipKey = (f: RunFilter): string =>
		'filters' in f ? (f.filters[0] ? chipKey(f.filters[0]) : '') : f.key;

	function filterLevel(): Level {
		// Which columns lead is settled as it opens: one set while it's open stays where it was
		// (Harry: setting several, nothing should move).
		const inUse = () =>
			new Set(keyboard.filters ? chipSegments(keyboard.filters.applied()).flat().map(chipKey) : []);
		// Frozen while this list is in view; read again on coming back to it.
		let usedAtOpen = inUse();
		// The switch, once shown, stays while the list is open (a filter removed doesn't shift it).
		let matchAtOpen = usedAtOpen.size > 1;
		return {
			label: 'Filter',
			refresh: () => {
				usedAtOpen = inUse();
				matchAtOpen = usedAtOpen.size > 1;
			},
			rows: () => {
				const f = keyboard.filters;
				if (!f) return [];
				// What's in use is read from the chips, which see filters joined with or too.
				const segments = chipSegments(f.applied());
				const chips = segments.flat();
				const chipOf = (key: string) => chips.findIndex((x) => chipKey(x) === key);
				// A filter set inside joins In use as it arrives; one removed here keeps its place.
				for (const x of chips) usedAtOpen.add(chipKey(x));
				const entry = (c: PaletteColumn): Entry => {
					const k = chipOf(c.key);
					return {
						label: c.label,
						keywords: [c.key],
						detail: k >= 0 ? f.describe(chips[k]) : undefined,
						icon: KIND_ICONS[c.kind ?? 'str'] ?? ListFilterIcon,
						level: () => columnFilterLevel(c),
						// ⌃X: this filter goes, the others stay as they're joined.
						remove:
							k >= 0
								? () => {
										delete ticks[c.key];
										f.set(joinSegments(dropChip(segments, k)));
									}
								: undefined
					};
				};
				const out: Sections = [];
				// With two filters or more, one switch: all of them, or any (the chips' joiners).
				if (chips.length > 1 || matchAtOpen) {
					const mode =
						segments.length === 1 ? 'any' : segments.every((x) => x.length === 1) ? 'all' : 'mixed';
					out.push([
						'Match',
						[
							{
								label:
									mode === 'any'
										? 'Match any filter'
										: mode === 'all'
											? 'Match all filters'
											: 'Match some with or',
								keywords: ['and', 'or', 'any', 'all', 'match'],
								detail: mode === 'all' ? 'and' : 'or',
								icon: ArrowLeftRightIcon,
								stay: true,
								run: () => f.set(joinSegments(mode === 'all' ? [chips] : chips.map((x) => [x])))
							}
						]
					]);
				}
				const cols = f.columns();
				const used = cols.filter((c) => usedAtOpen.has(c.key));
				if (used.length) out.push(['In use', used.map(entry)]);
				out.push(['Filter by', cols.filter((c) => !usedAtOpen.has(c.key)).map(entry)]);
				out.push([
					'More',
					[
						{
							label: 'Advanced (and / or)…',
							keywords: ['group'],
							run: f.advanced,
							icon: BracesIcon
						},
						...(f.active()
							? [
									{
										label: 'Clear all filters',
										keywords: ['reset'],
										run: f.clear,
										icon: XIcon,
										stay: true
									}
								]
							: [])
					]
				]);
				return out;
			}
		};
	}

	/** A checklist's ticks while it's open, ahead of the URL catching up. */
	let ticks = $state<Record<string, string[]>>({});

	/** One column's filter: its values to tick, or a value to type. */
	function columnFilterLevel(c: PaletteColumn): Level {
		delete ticks[c.key];
		const props = () => keyboard.filters!.props(c.key);
		const clear = () => {
			delete ticks[c.key];
			props().onclear();
		};
		// Clearing stays: the next value may follow (Harry).
		const clearRow = (p: ColumnFilterProps): Sections =>
			p.active
				? [[' ', [{ label: `Clear ${c.label} filter`, run: clear, icon: XIcon, stay: true }]]]
				: [];
		const first = props();
		if (first.kind === 'values')
			return {
				label: c.label,
				checklist: true,
				clear,
				rows: () => {
					const p = props();
					const ticked = new Set(ticks[c.key] ?? p.selected);
					const values: FilterValue[] = [
						...(p.values ?? []),
						...[...ticked]
							.filter((v) => !p.values?.some((o) => o.value === v))
							.map((v) => ({ value: v, label: v }))
					];
					return [
						[
							c.label,
							values.map((v): Entry => ({
								label: v.label,
								keywords: [v.value],
								detail: v.count != null ? String(v.count) : undefined,
								dot: v.dot,
								checked: ticked.has(v.value),
								toggle: () => {
									// Ticks made here lead: two quick ones land before the URL has the first.
									const was = ticks[c.key] ?? props().selected ?? [];
									const next = was.includes(v.value)
										? was.filter((x) => x !== v.value)
										: [...was, v.value];
									ticks[c.key] = next;
									props().onselect?.(next);
								}
							}))
						],
						...clearRow(p)
					];
				}
			};
		const range = first.kind === 'range';
		const date = !!first.date;
		return {
			label: c.label,
			input: {
				ops: range ? (date ? ['from', 'to'] : ['≥', '≤']) : ['contains'],
				placeholder: date ? '7d, yesterday, 2026-11-20…' : range ? 'A number…' : 'Text…',
				apply: (text, op) => {
					const p = props();
					if (!range) {
						p.ontext?.(text.trim());
						return true;
					}
					const value = date ? parseWhen(text) : text.trim();
					if (value == null) return false;
					p.onrange?.({ ...p.range, [op === 0 ? 'min' : 'max']: value });
					return true;
				}
			},
			rows: () => {
				const p = props();
				const now = summary(p);
				return now
					? [['Now', [{ label: `${c.label} ${now}`, icon: ListFilterIcon }]], ...clearRow(p)]
					: [];
			}
		};
	}

	/** Sort: the columns shown, the one sorting first; Enter opens its two ways. */
	function sortLevel(): Level {
		const firstAtOpen = keyboard.sort?.current().key;
		return {
			label: 'Sort',
			rows: () => {
				const s = keyboard.sort;
				if (!s) return [];
				const now = s.current();
				const cols = s.columns();
				const entry = (c: PaletteColumn): Entry => ({
					label: c.label,
					keywords: [c.key],
					detail: c.key === now.key ? orderWords(c.kind)[now.order] : undefined,
					checked: c.key === now.key,
					level: () => sortColumnLevel(c)
				});
				const out: Sections = [
					[
						'Sort by',
						[
							...cols.filter((c) => c.key === firstAtOpen),
							...cols.filter((c) => c.key !== firstAtOpen)
						].map(entry)
					]
				];
				if (!now.byDefault)
					out.push([
						'More',
						[{ label: 'Reset sort', keywords: ['default'], run: s.reset, icon: XIcon }]
					]);
				return out;
			}
		};
	}

	/** A column's two ways, the one it sorts by naturally first (a loss lowest, a score highest). */
	function sortColumnLevel(c: PaletteColumn): Level {
		const natural = naturalOrder(c.key, c.kind);
		const ways: SortOrder[] = natural === 'asc' ? ['asc', 'desc'] : ['desc', 'asc'];
		return {
			label: c.label,
			rows: () => {
				const s = keyboard.sort;
				if (!s) return [];
				const now = s.current();
				const words = orderWords(c.kind);
				return [
					[
						c.label,
						ways.map((o) => ({
							label: words[o],
							keywords: [o === 'asc' ? 'ascending' : 'descending'],
							icon: o === 'asc' ? ArrowUpIcon : ArrowDownIcon,
							detail: now.key === c.key && now.order === o ? 'Now' : undefined,
							run: () => s.set(c.key, o)
						}))
					]
				];
			}
		};
	}

	const KIND_ICONS: Partial<Record<string, Component>> = {
		num: HashIcon,
		duration: TimerIcon,
		time: CalendarIcon,
		user: UserIcon,
		state: CircleDotIcon,
		str: TypeIcon,
		bool: ToggleLeftIcon,
		json: BracesIcon
	};

	// ---- The selection, and comparing two eval runs ---------------------------------------

	/** Tags ticked or not here, ahead of the page reading the runs again. */
	let tagged = $state<Record<string, boolean>>({});
	/** The tag a first ⌃X asked to delete; any other key lets it go. */
	let deleting = $state<string | null>(null);

	/** The selection's tags: every tag of the project, ticked where all the runs have it. */
	function tagLevel(): Level {
		tagged = {};
		return {
			label: 'Tags',
			checklist: true,
			rows: () => {
				const sel = keyboard.selection;
				if (!sel) return [];
				const list = sel.tags();
				const names = new Set([...list.map((t) => t.tag), ...Object.keys(tagged)]);
				const set = (tag: string, on: boolean) => {
					tagged[tag] = on;
					sel.tag(tag, on);
				};
				const rows: Entry[] = [...names].sort().map((tag) => {
					const found = list.find((t) => t.tag === tag);
					const on = found?.on ?? 0;
					const runs = found?.runs ?? 0;
					const checked = tagged[tag] ?? on === sel.count;
					return {
						label: tag,
						checked,
						// ⌃X deletes the tag from the whole project (Enter only unticks): asked once,
						// as the row, then a second ⌃X does it.
						remove: () => {
							if (deleting !== tag) return void (deleting = tag);
							deleting = null;
							delete tagged[tag];
							sel.deleteTag(tag);
						},
						removeLabel: deleting === tag ? 'Delete' : 'Delete tag',
						danger: deleting === tag,
						detail:
							deleting === tag
								? `Delete from ${runs === 1 ? '1 run' : `${runs} runs`}? ⌃X again`
								: tagged[tag] === undefined && on > 0 && on < sel.count
									? `${on} of ${sel.count}`
									: undefined,
						toggle: () => set(tag, !checked)
					};
				});
				// A tag typed that isn't one yet: tick to make it.
				// A tag typed that isn't one yet: Create, the list's last row (alone when nothing
				// else matches, so Enter's).
				const typed = query.trim();
				if (typed && !/[\s,]/.test(typed) && !names.has(typed))
					rows.push({
						label: `Create “${typed}”`,
						keywords: [CREATE, typed],
						icon: PlusIcon,
						create: true,
						checked: false,
						toggle: () => set(typed, true)
					});
				return [['Tags', rows]];
			}
		};
	}

	/** The project's groups, to add the selection to; New group… names one. */
	function groupLevel(): Level {
		return {
			label: 'Group',
			rows: () => {
				const sel = keyboard.selection;
				if (!sel) return [];
				const list = sel.groups() ?? [];
				const typed = query.trim();
				const rows: Entry[] = list.map((g) => ({
					label: g.name,
					color: g.color,
					run: () => sel.addToGroup(g.id)
				}));
				// A name no group has: Create, the list's last row (alone when nothing else
				// matches, so Enter's).
				const create: Entry[] =
					typed && !list.some((g) => g.name.toLowerCase() === typed.toLowerCase())
						? [
								{
									label: `Create “${typed}”…`,
									keywords: [CREATE, typed],
									icon: PlusIcon,
									create: true,
									run: () => sel.newGroup(typed)
								}
							]
						: [];
				return [
					['Groups', [...rows, ...create]],
					['More', [{ label: 'New group…', icon: PlusIcon, run: () => sel.newGroup() }]]
				];
			}
		};
	}

	/** Compare, step by step: run A, then B (both eval runs here), then the benchmark. */
	function compareLevel(c: PaletteCompare, a?: { id: string; label: string }): Level {
		return {
			label: a ? `${a.label} vs` : 'Compare',
			searched: true,
			async load(q, signal) {
				const base = `/api/projects/${encodeURIComponent(c.entity)}/${encodeURIComponent(c.project)}`;
				const filter = flockFilter(q, [{ key: 'job_type', op: 'eq', value: 'eval' }]);
				const runs = await getJson<{ ids: string[]; data: Record<string, unknown[]> }>(
					`${base}/runs/query`,
					signal,
					{
						method: 'POST',
						headers: { 'content-type': 'application/json' },
						body: JSON.stringify({
							filter,
							sort: ['-created_at'],
							limit: 50,
							columns: ['display_name', 'state']
						})
					}
				);
				const rows = runs.ids
					.map((id, i) => ({
						id,
						label: String(runs.data.display_name?.[i] ?? id),
						state: String(runs.data.state?.[i] ?? '')
					}))
					.filter((r) => r.id !== a?.id);
				return [
					[
						a ? 'Run B' : 'Run A',
						rows.map((r) => ({
							label: r.label,
							keywords: [r.id],
							state: r.state,
							level: () => (a ? benchmarkLevel(c, a, r) : compareLevel(c, r))
						}))
					]
				];
			}
		};
	}

	/** The benchmarks both runs scored, each a way into their compare page. */
	function benchmarkLevel(
		c: PaletteCompare,
		a: { id: string; label: string },
		b: { id: string; label: string }
	): Level {
		const href = compareHref(c.entity, c.project, a.id, `${c.entity}/${c.project}/${b.id}`);
		return {
			label: b.label,
			async load(_, signal) {
				const base = `/api/projects/${encodeURIComponent(c.entity)}/${encodeURIComponent(c.project)}`;
				const found = await getJson<ItemSuites>(
					`${base}/runs/${encodeURIComponent(a.id)}/items/suites?with=${encodeURIComponent(`${c.entity}/${c.project}/${b.id}`)}`,
					signal
				).catch(() => null);
				const out: Sections = [
					['Compare', [{ label: 'All benchmarks', href, icon: GitCompareArrowsIcon }]]
				];
				for (const suite of found?.suites ?? [])
					out.push([
						suite.suite,
						suite.tasks.map((task) => ({
							label: task,
							keywords: [suite.suite],
							href: `${href}&task=${encodeURIComponent(task)}`,
							icon: ListChecksIcon
						}))
					]);
				return out;
			}
		};
	}

	/**
	 * Tag runs… or Add runs to group… with nothing selected: tick the runs here (the table's
	 * selection ticks with them), then go on to the tags or the groups.
	 */
	function pickLevel(then: 'tag' | 'group'): Level {
		return {
			label: then === 'tag' ? 'Tag runs' : 'Add to group',
			checklist: true,
			rows: () => {
				const pick = keyboard.pick;
				if (!pick) return [];
				const runs = pick.runs();
				const n = runs.filter((r) => r.on).length;
				const what = n === 1 ? 'run' : `${n} runs`;
				return [
					[
						'Next',
						[
							{
								label: n
									? then === 'tag'
										? `Tag ${what}…`
										: `Add ${what} to group…`
									: 'Tick the runs first',
								icon: then === 'tag' ? TagIcon : LayersIcon,
								level: n ? (then === 'tag' ? tagLevel : groupLevel) : undefined
							}
						]
					],
					[
						'Runs',
						runs.map((r) => ({
							label: r.label,
							keywords: [r.id],
							state: r.state,
							checked: r.on,
							toggle: () => pick.toggle(r.id)
						}))
					]
				];
			}
		};
	}

	/** The page's views, the one shown ticked. */
	function viewLevel(): Level {
		return {
			label: 'View',
			rows: () => [
				[
					'View',
					keyboard.views.flatMap((v): Entry[] =>
						v.layouts
							? v.layouts.map((l, i) => ({
									label: `${v.label} · ${l.label}`,
									keywords: [v.label, l.label],
									run: l.set,
									hint: i === 0 ? v.key.toUpperCase() : undefined,
									icon: l.label === 'stacked' ? Rows2Icon : VIEW_ICONS[v.key],
									detail: l.on ? 'Now' : undefined
								}))
							: [
									{
										label: v.label,
										run: v.set,
										hint: v.key.toUpperCase(),
										icon: VIEW_ICONS[v.key],
										detail: v.on ? 'Now' : undefined
									}
								]
					)
				]
			]
		};
	}

	/** `/` first: only what can be done here, not places to go. */
	function actionsLevel(): Level {
		return {
			label: 'Actions',
			rows: () =>
				top.filter(([name]) => name === 'Selected' || name === 'This page' || name === 'Appearance')
		};
	}

	/** The org's buckets. */
	const storageLevel = (o: string): Level => ({
		label: 'Storage',
		async load(_, signal) {
			const b = await getJson<{ buckets: Bucket[] }>(
				`/api/storage/${encodeURIComponent(o)}`,
				signal
			);
			return [
				[
					'Buckets',
					b.buckets
						.filter((x) => x.deleted_at === undefined || x.deleted_at === null)
						.map((x): Entry => ({
							label: x.name,
							href: objectHref(o, x.name),
							icon: HardDriveIcon,
							level: () => bucketLevel(o, x.name, '')
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

	// Every dataset and model in the org's folders, found by walking them in the background once
	// the menu opens (Flock has no search across folders), so typing finds one without Tab. Kept
	// to the first 60 folders read, four at a time.
	let deep = $state<{ org: string; datasets: Entry[]; models: Entry[] } | null>(null);
	$effect(() => {
		if (!open || !org || !shell?.s3 || deep?.org === org) return;
		const o = org;
		const controller = new AbortController();
		const { signal } = controller;
		const found = { org: o, datasets: [] as Entry[], models: [] as Entry[] };
		let budget = 60;
		async function walk(paths: string[], read: (path: string) => Promise<string[]>) {
			const queue = [...paths];
			const worker = async () => {
				while (queue.length && budget > 0 && !signal.aborted) {
					budget--;
					queue.push(...(await read(queue.shift()!).catch(() => [])));
				}
			};
			await Promise.all([worker(), worker()]);
		}
		void Promise.all([
			walk([''], async (path) => {
				const at = await datasetCall<DatasetFolder | DatasetAt>(datasetsApi(o, 'at', { path }), {
					signal
				});
				if (at.kind !== 'folder') return [];
				for (const d of at.datasets)
					found.datasets.push({
						label: d.name,
						keywords: [d.path],
						detail: d.path.includes('/') ? d.path.slice(0, d.path.lastIndexOf('/')) : undefined,
						href: datasetsHref(o, d.path),
						icon: DatabaseIcon,
						level: () => datasetItemLevel(o, d.path, d.version)
					});
				return at.folders.map((f) => f.path);
			}),
			walk([''], async (path) => {
				const at = await datasetCall<ModelFolder | ModelAt>(folderApi(o, path), { signal });
				if (at.kind !== 'folder') return [];
				for (const m of at.models)
					found.models.push({
						label: nameOf(m.name),
						keywords: [m.name],
						detail: m.name.includes('/') ? m.name.slice(0, m.name.lastIndexOf('/')) : undefined,
						href: modelsHref(o, m.name),
						icon: BoxIcon,
						level: () => modelItemLevel(o, m.name, m.primary)
					});
				return at.folders.map((f) => f.path);
			})
		]).then(() => {
			if (!signal.aborted) deep = found;
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

	/** A recent page that opens in place too: a project (its groups and runs), a run (its tabs). */
	function visitLevel(v: Visit): (() => Level) | undefined {
		const [, o, p, kind, id] = pathOf(v.href).split('/').map(decodeURIComponent);
		if (!o || !p) return undefined;
		if (v.kind === 'project' && !kind) return () => projectLevel(o, p);
		if (v.kind === 'run' && kind === 'runs' && id) return () => runLevel(o, p, id, v.label);
		return undefined;
	}

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
				const o = org;
				// Each opens in place too (Tab), listing what's at its top.
				go.push({
					label: 'Datasets',
					href: datasetsHref(o),
					hint: 'G D',
					icon: DatabaseIcon,
					level: () => ({ ...datasetLevel(o, ''), label: 'Datasets' })
				});
				go.push({
					label: 'Models',
					href: modelsHref(o),
					hint: 'G M',
					icon: BoxIcon,
					level: () => ({ ...modelLevel(o, ''), label: 'Models' })
				});
				go.push({
					label: 'Storage',
					href: storageHref(o),
					hint: 'G S',
					icon: HardDriveIcon,
					level: () => storageLevel(o)
				});
			}
			go.push({ label: 'Settings', href: orgHref(org), hint: 'G ,', icon: SettingsIcon });
		}
		go.push({ label: 'Account and keys', href: resolve('/(app)/account'), icon: UserIcon });

		const out: Sections = [];
		const here: Entry[] = [
			...(keyboard.filters
				? [
						{
							label: 'Filter…',
							keywords: ['filter', 'where'],
							hint: 'F',
							icon: ListFilterIcon,
							level: filterLevel
						}
					]
				: keyboard.filter
					? [{ label: 'Filter runs', run: keyboard.filter, hint: 'F', icon: ListFilterIcon }]
					: []),
			// With runs selected, the Selected section tags them; these pick runs first.
			...(keyboard.pick && !keyboard.selection
				? [
						{
							label: 'Tag runs…',
							keywords: ['tags', 'label'],
							icon: TagIcon,
							level: () => pickLevel('tag')
						},
						...(groups.length
							? [
									{
										label: 'Add runs to group…',
										keywords: ['group'],
										icon: LayersIcon,
										level: () => pickLevel('group')
									}
								]
							: [])
					]
				: []),
			...(keyboard.compare
				? [
						{
							label: 'Compare evals…',
							keywords: ['compare', 'diff', 'versus', 'vs'],
							icon: GitCompareArrowsIcon,
							level: (
								(c: PaletteCompare) => () =>
									compareLevel(c)
							)(keyboard.compare)
						}
					]
				: []),
			...(keyboard.sort
				? [
						{
							label: 'Sort…',
							keywords: ['sort', 'order'],
							hint: '⇧ S',
							icon: ArrowUpDownIcon,
							level: sortLevel
						}
					]
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
		const visits = recent.all
			.filter(
				(v, i, all) =>
					pathOf(v.href) !== page.url.pathname &&
					all.findIndex((w) => pathOf(w.href) === pathOf(v.href)) === i
			)
			.slice(0, 5);
		// Home leads with what was opened lately; any other page with its own commands (Harry).
		// The views as one row, last: what can be done to the runs leads (Harry). Typing a view's
		// name finds it.
		if (keyboard.views.length)
			here.push({
				label: 'View…',
				keywords: [
					'view',
					'show',
					'layout',
					...keyboard.views.flatMap((v) => [
						v.label.toLowerCase(),
						...(v.layouts ?? []).map((l) => l.label)
					])
				],
				icon: VIEW_ICONS[keyboard.views.find((v) => v.on)?.key ?? 't'] ?? TableIcon,
				detail: keyboard.views.find((v) => v.on)?.label,
				level: viewLevel
			});
		const home = page.route?.id === '/(app)';
		// Runs selected: what to do with them leads.
		const sel = keyboard.selection;
		if (sel) {
			const what = sel.count === 1 ? 'run' : `${sel.count} runs`;
			out.push([
				'Selected',
				[
					// Enter, then Enter again on All benchmarks; or pick one.
					...(sel.pair && keyboard.compare
						? [
								{
									label: `Compare ${what}…`,
									keywords: ['compare', 'diff', 'vs'],
									icon: GitCompareArrowsIcon,
									level: ((c: PaletteCompare, pair: NonNullable<typeof sel.pair>) => () => ({
										...benchmarkLevel(c, pair.a, pair.b),
										label: `${pair.a.label} vs ${pair.b.label}`
									}))(keyboard.compare, sel.pair)
								}
							]
						: []),
					{ label: `Tag ${what}…`, keywords: ['tags', 'label'], icon: TagIcon, level: tagLevel },
					...(sel.groups()
						? [
								{
									label: `Add ${what} to group…`,
									keywords: ['group'],
									icon: LayersIcon,
									level: groupLevel
								}
							]
						: [])
				]
			]);
		}
		if (here.length && !home) out.push(['This page', here]);
		if (visits.length)
			out.push([
				'Recent',
				visits.map((v): Entry => ({
					label: v.label,
					href: v.href,
					detail: v.detail,
					keywords: v.detail ? [v.detail] : [],
					icon: VISIT_ICONS[v.kind],
					level: visitLevel(v)
				}))
			]);
		if (here.length && home) out.push(['This page', here]);
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
		// Found only by typing: listed whole, they'd bury everything else.
		if (query.trim() && deep && deep.org === org) {
			if (deep.datasets.length) out.push(['Datasets', deep.datasets]);
			if (deep.models.length) out.push(['Models', deep.models]);
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
		if (!level) return peeking ? top.slice(0, 1) : matched(top, true);
		// A value typed into an input level is not a search.
		if (level.input) return level.rows?.() ?? inside;
		if (level.rows) return matched(level.rows(), true);
		// Flock searched these already, in its own order: only narrowed here while the next
		// answer is on its way.
		return matched(inside, !level.searched);
	}

	/**
	 * The rows matching what's typed, here rather than by the list (bits-ui), which sorts by
	 * moving rows in the page under Svelte and so leaves stale ones behind. Sorted (best first,
	 * and the sections by their best) unless `sort` is off; Create rows stay, last.
	 */
	function matched(all: Sections, sort: boolean): Sections {
		const q = query.trim();
		if (!q) return all;
		const scored = all
			.map(([name, entries]) => {
				const rows = entries
					.map((e) => ({ e, score: filter(`${name}/`, q, [e.label, ...(e.keywords ?? [])]) }))
					.filter((x) => x.score > 0);
				if (sort) rows.sort((a, b) => b.score - a.score);
				return { name, rows, best: rows[0] ? Math.max(...rows.map((x) => x.score)) : 0 };
			})
			.filter((x) => x.rows.length);
		if (sort) scored.sort((a, b) => b.best - a.best);
		return scored.map((x) => [x.name, x.rows.map((r) => r.e)]);
	}
	/**
	 * Each row's value, unique in the list: its section and name (a second row of the same name
	 * numbered). Not its place: the list keeps a row's words by its value, so a row that took
	 * another's place (a list grown or shrunk) would be matched with the other's words.
	 */
	const keyed = $derived(
		Object.fromEntries(
			sections.map(([name, entries]) => {
				const seen: Record<string, number> = {};
				const keys = entries.map((e) => {
					const base = `${name}/${e.label}`;
					const n = (seen[base] = (seen[base] ?? 0) + 1) - 1;
					return n ? `${base}#${n}` : base;
				});
				return [name, keys];
			})
		) as Record<string, string[]>
	);
	const valueOf = (section: string, i: number) => keyed[section]?.[i] ?? `${section}/${i}`;
	const activeEntry = $derived.by(() => {
		for (const [name, entries] of sections) {
			const i = keyed[name]?.indexOf(active) ?? -1;
			if (i >= 0) return entries[i];
		}
		return null;
	});

	// What's in the level open: read again as the search changes where Flock searches it.
	const levelQuery = $derived(level?.searched ? query.trim() : '');
	$effect(() => {
		const l = level;
		if (!l?.load) return;
		const load = l.load;
		const q = levelQuery;
		const controller = new AbortController();
		loading = true;
		failed = false;
		const timer = setTimeout(
			() => {
				load(q, controller.signal)
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

	/** Which way the list last moved: 1 into a level, -1 back out (its slide). */
	let dir = $state(1);
	const still =
		typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches;
	function enter(e: Entry) {
		if (!e.level) return;
		held = null;
		dir = 1;
		op = 0;
		inside = [];
		const next = e.level();
		stack = [...stack, next];
		// Found by one of its rows' names ("charts" → View…): the cursor starts on that row.
		const typed = query.trim();
		query = '';
		if (typed && next.rows && !next.checklist) {
			let best = '';
			let top = 0;
			for (const [, entries] of next.rows())
				for (const x of entries) {
					const score = searchScore(typed, x.label, x.keywords);
					if (score > top) [best, top] = [x.label, score];
				}
			if (best) holdOn(best);
		}
	}
	function up() {
		dir = -1;
		op = 0;
		const left = stack.at(-1)?.label ?? null;
		cameFrom = left;
		inside = [];
		stack = stack.slice(0, -1);
		// The level come back to reads what changed inside (a filter set shows as in use)…
		stack.at(-1)?.refresh?.();
		query = '';
		// …and the cursor lands on the row just left.
		if (left && stack.length) holdOn(left);
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

	/** The way an input level applies (≥ or ≤, from or to), flipped by Tab. */
	let op = $state(0);
	/** Opened inside Filter or Sort (F, Shift+S): Escape there closes, as it opened. */
	let started = false;
	$effect(() => {
		if (!open || !start) return;
		const at = start;
		start = null;
		dir = 1;
		inside = [];
		query = '';
		stack = [at === 'filter' ? filterLevel() : sortLevel()];
		started = true;
	});

	// Closed, the menu starts again at the top.
	$effect(() => {
		if (open) return;
		reset();
	});
	function reset() {
		started = false;
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

	/**
	 * The row the cursor stays on after a tick, by label, until a key moves it or a search starts:
	 * the tick reloads the runs, the rows are rebuilt, and the list would go back to its first.
	 */
	let held = $state<string | null>(null);
	/** A key just ticked a value: the list's own Enter, a moment later, mustn't tick it back. */
	let tickedByKey = false;
	const holdOn = (label: string) => (held = label);
	$effect(() => {
		const label = held;
		if (!label) return;
		for (const [name, entries] of sections) {
			const i = entries.findIndex((x) => x.label === label);
			if (i < 0) continue;
			const value = valueOf(name, i);
			if (active !== value) requestAnimationFrame(() => (active = value));
			return;
		}
	});

	/**
	 * The value a key ticks: with a search, its best match, read from the search itself (typed
	 * fast, the highlight still sits on the row before the list narrowed); else the highlight.
	 */
	function checklistTarget(): Entry | null {
		const q = query.trim();
		if (!q) return activeEntry?.toggle ? activeEntry : null;
		// The best match among what's there; Create only when nothing is.
		let best: Entry | null = null;
		let create: Entry | null = null;
		let top = 0;
		for (const [, entries] of sections)
			for (const e of entries) {
				if (!e.toggle) continue;
				if (e.create) {
					create = e;
					continue;
				}
				const score = searchScore(q, e.label, e.keywords);
				if (score > top) [best, top] = [e, score];
			}
		return best ?? create;
	}

	function onkeydown(e: KeyboardEvent) {
		const field = e.currentTarget as HTMLInputElement;
		const atEnd = field.selectionStart === field.value.length;
		// A key that moves the cursor or starts a search lets go of the row held.
		if (e.key.startsWith('Arrow') || e.key.length === 1 || e.key === 'Backspace') held = null;
		const ctrlX = e.ctrlKey && !e.metaKey && e.key.toLowerCase() === 'x';
		// Any key but ⌃X (and the modifiers on their way to it) lets a pending delete go.
		if (!ctrlX && !['Control', 'Shift', 'Alt', 'Meta'].includes(e.key)) deleting = null;
		if (ctrlX) {
			const remove = activeEntry?.remove ?? level?.clear;
			if (remove) {
				e.preventDefault();
				remove();
				return;
			}
		}
		if (e.key === '/' && !query && !stack.length && visible) {
			e.preventDefault();
			dir = 1;
			stack = [actionsLevel()];
			return;
		}
		if (level?.input) {
			if (e.key === 'Tab' && level.input.ops.length > 1) {
				e.preventDefault();
				op = (op + 1) % level.input.ops.length;
				return;
			}
			if (e.key === 'Enter' && query.trim()) {
				e.preventDefault();
				e.stopPropagation();
				// Applied, back to the list it came from, for the next filter (Harry): not closed.
				if (level.input.apply(query, op)) {
					if (stack.length > 1) up();
					else finish();
				}
				return;
			}
		}
		if (level?.checklist) {
			const target = checklistTarget();
			// Enter ticks or unticks and stays (Harry), as Space and Tab do; Escape is done.
			const tick =
				e.key === 'Enter' || (e.key === ' ' && !query) || (e.key === 'Tab' && !e.shiftKey);
			if (target && tick) {
				e.preventDefault();
				e.stopPropagation();
				// The list answers Enter on the same field too; this key's tick is the one.
				tickedByKey = true;
				setTimeout(() => (tickedByKey = false));
				target.toggle?.();
				// After a search, ticking starts the next one, the cursor staying on the row
				// just ticked rather than jumping to the top.
				query = '';
				holdOn(target.label);
				return;
			}
		}
		if ((e.key === 'Tab' && !e.shiftKey) || (e.key === 'ArrowRight' && atEnd)) {
			if (!visible) return;
			if (activeEntry?.level) {
				e.preventDefault();
				enter(activeEntry);
			} else if (e.key === 'Tab') e.preventDefault();
		} else if (
			stack.length &&
			// Backspace goes back on a fresh press only: held to clear the search, it stops once
			// the text is gone instead of running on up the levels (Harry).
			((e.key === 'Backspace' && !query && !e.repeat) ||
				// ← at the field's start goes back, as → at its end goes in.
				(e.key === 'ArrowLeft' && field.selectionStart === 0 && field.selectionEnd === 0))
		) {
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
		else if (stack.length === 1 && started) return false;
		// Ticking is done: Escape closes, as ⌘K does (Harry); Backspace still goes back.
		else if (level?.checklist && !inline) return false;
		else if (stack.length) up();
		else if (inline && (expanded || peek)) {
			expanded = false;
			peek = false;
		} else return false;
		return true;
	}

	function choose(e: Entry) {
		// A value ticks or unticks, and the list stays for the next.
		if (e.toggle) {
			if (!tickedByKey) e.toggle();
			return;
		}
		if (!e.run && !e.href) {
			if (e.level) enter(e);
			return;
		}
		if (e.run && e.stay) return e.run();
		if (inline) reset();
		else open = false;
		onclose?.();
		if (e.run) e.run();
		// eslint-disable-next-line svelte/no-navigation-without-resolve -- built with resolve()
		else if (e.href) void goto(e.href);
	}

	/** Done: the menu closes, as when a row is chosen. */
	function finish() {
		if (inline) reset();
		else open = false;
		onclose?.();
	}

	/** Marks a Create row's words, so the list keeps it last (it matches its own text fully). */
	const CREATE = '\u0001create';

	/** Matches on the label and the row's own words, not on its value (see search.ts). */
	const filter = (value: string, search: string, keywords?: string[]) => {
		// Create is always shown while something is typed, and always last among matches.
		if (keywords?.includes(CREATE)) return 0.0001;
		const score = searchScore(search, keywords?.[0] ?? '', keywords?.slice(1));
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
	// A level's rows sketched while it loads: in place from the start, shown after a moment.
	const sketching = $derived(loading && !sections.length);
	let sketchShown = $state(false);
	$effect(() => {
		if (!sketching) return void (sketchShown = false);
		const timer = setTimeout(() => (sketchShown = true), 200);
		return () => clearTimeout(timer);
	});
	const SKETCH = [46, 62, 38, 55, 70];
	/** The list's own height, which the box around it eases to. */
	let listHeight = $state(0);
	/**
	 * The bar along the foot (Raycast's): always there, the same height, so nothing jumps. On its
	 * right, what Enter does to the row in the cursor and the one other key that row answers to;
	 * only their words change as the cursor moves.
	 */
	const primary = $derived.by((): [string, string] => {
		if (level?.input) return ['Apply', '↵'];
		if (level?.checklist) {
			const target = checklistTarget();
			return [target?.create ? 'Create' : target?.checked ? 'Untick' : 'Tick', '↵'];
		}
		if (activeEntry?.create) return ['Create', '↵'];
		const e = activeEntry;
		if (!e) return ['Open', '↵'];
		if (e.run) return [e.checked === undefined ? 'Run' : 'Choose', '↵'];
		return ['Open', '↵'];
	});
	const secondary = $derived.by((): [string, string] | null => {
		if (level?.input)
			return level.input.ops.length > 1
				? [`Use ${level.input.ops[(op + 1) % level.input.ops.length]}`, 'Tab']
				: null;
		if (activeEntry?.remove) return [activeEntry.removeLabel ?? 'Remove', '⌃ X'];
		if (level?.checklist) return ['Done', 'Esc'];
		if (activeEntry?.level && (activeEntry.href || activeEntry.run)) return ['Look inside', 'Tab'];
		return null;
	});
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
	{#if level?.input}
		<!-- The way it applies; Tab flips it. -->
		<span class="flex-none text-muted-foreground">{level.input.ops[op]}</span>
	{/if}
{/snippet}

{#snippet field(cls: string)}
	<Command.Input
		bind:ref={input}
		class={cls}
		placeholder={level?.input
			? level.input.placeholder
			: level
				? `Search ${level.label}…`
				: 'Search or jump to…'}
		aria-label={level ? `Search ${level.label}` : 'Search or jump to'}
		bind:value={query}
		{onkeydown}
		{onfocus}
		onblur={inline ? onfocusout : undefined}
	/>
	{#if loading && !sketching}
		<LoaderCircleIcon class="size-3.5 flex-none animate-spin text-muted-foreground" />
	{/if}
{/snippet}

{#snippet list()}
	<!-- The box eases to the list's height as rows come and go, so nothing snaps. -->
	<div
		class="overflow-hidden transition-[height] duration-150 ease-[cubic-bezier(0.2,0.8,0.25,1)] motion-reduce:transition-none"
		style:height={listHeight ? `${listHeight}px` : undefined}
	>
		<div bind:offsetHeight={listHeight}>
			<Command.List class="max-h-[min(60vh,28rem)] overflow-y-auto p-1.5">
				<Command.Viewport>
					{#key stack.length}
						<!-- A level slides in from the side it was entered (in from the right, back from the
			     left) as it fades; nothing waits on it. -->
						<div in:fly={{ x: still ? 0 : dir * 8, duration: still ? 0 : 120, easing: cubicOut }}>
							{#if sketching}
								<div
									role="status"
									aria-label="Loading"
									class={['p-0.5', !sketchShown && 'invisible']}
								>
									<div class="px-2 pt-2 pb-1">
										<span class="block h-2 w-12 animate-pulse rounded-full bg-muted"></span>
									</div>
									{#each SKETCH as w, i (i)}
										<div class="flex h-8 items-center gap-2 px-2">
											<span class="size-3.5 flex-none animate-pulse rounded-[3px] bg-muted"></span>
											<span class="h-2.5 animate-pulse rounded-full bg-muted" style:width="{w}%"
											></span>
										</div>
									{/each}
								</div>
							{/if}
							{#if !loading}
								<Command.Empty class="px-2 py-6 text-center text-[13px] text-muted-foreground">
									{failed
										? 'Couldn’t load this'
										: level
											? 'Nothing here matches'
											: 'Nothing matches'}
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
													{#if e.checked !== undefined && level?.checklist}
														<span
															class={[
																'flex size-3.5 items-center justify-center rounded-[4px] ring-1',
																e.checked
																	? 'bg-foreground text-background ring-foreground'
																	: 'ring-foreground/25'
															]}
														>
															{#if e.checked}<CheckIcon class="size-2.5" strokeWidth={3} />{/if}
														</span>
													{:else if e.checked}
														<CheckIcon class="size-3.5 text-foreground" />
													{:else if e.state}
														<span class={['size-1.5 rounded-full', DOT[e.state] ?? 'bg-faint']}
														></span>
													{:else if e.color}
														<span class="size-2 rounded-[2px]" style:background={e.color}></span>
													{:else if e.icon}
														<e.icon class="size-3.5 text-muted-foreground" />
													{/if}
												</span>
												{#if e.dot && level?.checklist}
													<span
														class={['size-1.5 flex-none rounded-full', e.dot]}
														aria-hidden="true"
													></span>
												{/if}
												<!-- A row that opens in place: a chevron right after its name, so the keys on the
												     right stay in one column. -->
												<span class="flex min-w-0 flex-1 items-center gap-1">
													<span class="min-w-0 truncate">{e.label}</span>
													{#if e.level}
														<ChevronRightIcon
															class="size-3.5 flex-none text-faint"
															aria-hidden="true"
														/>
													{/if}
												</span>
												{#if e.detail}
													<span
														class={[
															'min-w-0 truncate',
															e.danger ? 'text-destructive' : 'max-w-40 text-muted-foreground'
														]}>{e.detail}</span
													>
												{/if}
												{#if e.hint}
													<Keys keys={e.hint} />
												{/if}
											</Command.Item>
										{/each}
									</Command.GroupItems>
								</Command.Group>
							{/each}
						</div>
					{/key}
				</Command.Viewport>
			</Command.List>
		</div>
	</div>
	<div
		class="flex h-9 items-center gap-3 border-t border-divider px-3.5 text-[12px] text-muted-foreground"
	>
		<span class="flex min-w-0 flex-1 items-center gap-1.5">
			{#if stack.length}
				<Keys keys="⌫" /> Back
			{/if}
		</span>
		<span
			class={[
				'flex items-center gap-1.5 transition-opacity duration-150',
				secondary ? 'opacity-100' : 'opacity-0'
			]}
			aria-hidden={!secondary}
		>
			{secondary?.[0] ?? 'Look inside'}
			<Keys keys={secondary?.[1] ?? 'Tab'} />
		</span>
		<span
			class={['h-3.5 w-px bg-divider transition-opacity duration-150', !secondary && 'opacity-0']}
			aria-hidden="true"
		></span>
		<span class="flex items-center gap-1.5 text-foreground">
			{primary[0]}
			<Keys keys={primary[1]} />
		</span>
	</div>
{/snippet}

<!-- The rows come matched and ordered (`matched`): the list itself neither filters nor sorts. -->
<Command.Root loop class="min-w-0" bind:value={active} shouldFilter={false}>
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
