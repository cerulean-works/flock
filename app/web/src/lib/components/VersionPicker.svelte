<script lang="ts">
	import TagIcon from '@lucide/svelte/icons/tag';
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
		/** Reads the newest versions, each with the names on it (`primary`, `latest`, tags). */
		read: (signal: AbortSignal) => Promise<{ number: number; names: string[] }[]>;
		/** The page's URL at another `?version=` (null: the default version). */
		versionHref: (version: string | null) => ResolvedPathname;
	}

	let { version, requested, tags, primary = null, read, versionHref }: Props = $props();

	/** A tag going to the version it's on, or a version by its number. */
	interface Choice {
		/** The `?version=` it sets. */
		value: string | null;
		tag: string | null;
		version: number;
		/** The names on a version. */
		names: string[];
	}

	let open = $state(false);
	let query = $state('');
	let active = $state(0);
	let versions = $state<{ number: number; names: string[] }[] | null>(null);
	let failed = $state<string | null>(null);

	const tagged = $derived(requested !== null && !/^\d+$/.test(requested) ? requested : null);

	// The tags first, then the versions, narrowed by the search.
	const choices = $derived.by(() => {
		const q = query.trim().toLowerCase();
		const all: Choice[] = [];
		if (primary !== null) all.push({ value: null, tag: 'primary', version: primary, names: [] });
		for (const t of tags) all.push({ value: t.name, tag: t.name, version: t.version, names: [] });
		for (const v of versions ?? []) {
			all.push({ value: String(v.number), tag: null, version: v.number, names: v.names });
		}
		const shown: Choice[] = [];
		for (const c of all) {
			const text = c.tag ?? `version ${c.version} ${c.names.join(' ')}`;
			if (text.toLowerCase().includes(q)) shown.push(c);
		}
		return shown;
	});
	const current = (c: Choice) => c.tag === null && c.version === version;

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
		void goto(versionHref(choice.value), { reset: false });
	}
</script>

<PickerField bind:open {onOpenChange} class="h-7 w-auto gap-1.5 text-[13px]">
	<span class="tabular-nums">Version {version}</span>
	{#if tagged}
		<span class="ml-1 inline-flex items-center gap-1 text-muted-foreground">
			<TagIcon class="size-3" />{tagged}
		</span>
	{/if}
	{#snippet list()}
		<PickerList
			options={choices}
			key={(c) => `${c.tag ?? ''}@${c.version}`}
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
				{#if c.tag !== null}
					<TagIcon class="size-3.5 flex-none text-muted-foreground" />
					<span class="min-w-0 flex-1 truncate">{c.tag}</span>
					<span class="flex-none text-xs text-muted-foreground tabular-nums"
						>version {c.version}</span
					>
				{:else}
					<span class="flex-none tabular-nums">Version {c.version}</span>
					<span class="min-w-0 flex-1 truncate text-right text-xs text-muted-foreground"
						>{c.names.join(', ')}</span
					>
				{/if}
			{/snippet}
		</PickerList>
	{/snippet}
</PickerField>
