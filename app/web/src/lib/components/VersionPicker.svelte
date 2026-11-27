<script lang="ts">
	import { untrack } from 'svelte';
	import type { ResolvedPathname } from '$app/types';
	import { goto } from '$app/navigation';
	import PickerField from '#lib/components/PickerField.svelte';
	import PickerList from '#lib/components/PickerList.svelte';

	/**
	 * Goes to another version of a model or a dataset: by a tag (and the primary, for a model) or
	 * by its number. The page's URL keeps the choice.
	 */
	interface Props {
		/** The version shown. */
		version: number;
		/** What the URL asked for (a number or a tag), null for the default version. */
		requested: string | null;
		/** The tags, each with the version it's on, in the order to list them. */
		tags: readonly { name: string; version: number }[];
		/** The primary version's number, listed first as `primary`. Models have one. */
		primary?: number | null;
		/** A word after the version when no tag was asked for: `Primary`. */
		mark?: string | null;
		/** Reads the newest versions, each with the names on it (`primary`, `latest`, tags). */
		read: (signal: AbortSignal) => Promise<{ number: number; names: string[] }[]>;
		/** The page's URL at another `?version=` (null: the default version). */
		versionHref: (version: string | null) => ResolvedPathname;
	}

	let {
		version,
		requested,
		tags,
		primary = null,
		mark = null,
		read,
		versionHref
	}: Props = $props();

	/** A version, by its number, with the names on it (`primary`, `latest`, its tags). */
	interface Choice {
		version: number;
		names: string[];
	}

	let open = $state(false);
	let query = $state('');
	let active = $state(0);
	let versions = $state<{ number: number; names: string[] }[] | null>(null);
	let failed = $state<string | null>(null);

	const tagged = $derived(requested !== null && !/^\d+$/.test(requested) ? requested : null);

	// One list of versions, newest first, each once with its names (Gate 2). Tags and the primary
	// on versions older than the page read are added, so every name can still be found.
	const choices = $derived.by(() => {
		const q = query.trim().toLowerCase();
		const byNumber: Record<number, string[]> = {};
		for (const v of versions ?? []) byNumber[v.number] = [...v.names];
		const name = (n: number, label: string) => {
			const names = (byNumber[n] ??= []);
			if (!names.includes(label)) names.push(label);
		};
		if (versions !== null) {
			if (primary !== null) name(primary, 'primary');
			for (const t of tags) name(t.version, t.name);
		}
		const all: Choice[] = Object.entries(byNumber)
			.map(([n, names]) => ({ version: Number(n), names }))
			.sort((x, y) => y.version - x.version);
		return all.filter((c) =>
			`version ${c.version} v${c.version} ${c.names.join(' ')}`.toLowerCase().includes(q)
		);
	});
	const current = (c: Choice) => c.version === version;
	/** A version's names as words: Primary capitalised, the others as they are. */
	const words = (names: string[]) =>
		names.map((n) => (n === 'primary' ? 'Primary' : n)).join(' · ');

	// The newest versions, read when the list first opens.
	$effect(() => {
		if (!open || versions !== null) return;
		const controller = new AbortController();
		// The list starts on the version shown, unless the reader moved through the tags meanwhile.
		const start = untrack(() => active);
		read(controller.signal)
			.then((got) => {
				versions = got;
				if (!query && active === start) active = Math.max(0, choices.findIndex(current));
			})
			.catch((e: Error) => {
				if (!controller.signal.aborted) failed = e.message;
			});
		return () => controller.abort();
	});

	function onOpenChange(next: boolean) {
		if (!next) return;
		query = '';
		active = Math.max(0, choices.findIndex(current));
	}

	function onpick(choice: Choice) {
		open = false;
		// Found by a tag (or the primary), it goes by that name, so the link follows it.
		const q = query.trim().toLowerCase();
		const by = q ? choice.names.find((n) => n !== 'latest' && n.toLowerCase().includes(q)) : null;
		const value = by === 'primary' ? null : (by ?? String(choice.version));
		void goto(versionHref(value), { reset: false });
	}
</script>

<!-- A quiet control in the header's trail, beside the name: "v3 · Primary", "v3 · best". -->
<PickerField
	bind:open
	{onOpenChange}
	aria-label="Version {version}{(tagged ?? mark) ? `, ${tagged ?? mark}` : ''}"
	class="h-7 w-auto gap-1 border-transparent bg-transparent px-2 text-[13px] font-normal text-subtle hover:bg-muted hover:text-foreground data-[state=open]:border-transparent data-[state=open]:bg-muted dark:bg-transparent"
>
	<span class="tabular-nums"
		>v{version}{#if tagged ?? mark}<span class="text-muted-foreground"
				>&nbsp;·&nbsp;{tagged ?? mark}</span
			>{/if}</span
	>
	{#snippet list()}
		<PickerList
			options={choices}
			key={(c) => String(c.version)}
			{current}
			noun="versions"
			label="Search versions"
			placeholder="Search versions…"
			bind:query
			bind:active
			loading={versions === null}
			error={failed}
			{onpick}
		>
			{#snippet row(c: Choice)}
				<span class="flex-none tabular-nums">v{c.version}</span>
				<span class="min-w-0 flex-1 truncate text-right text-xs text-muted-foreground"
					>{words(c.names)}</span
				>
			{/snippet}
		</PickerList>
	{/snippet}
</PickerField>
