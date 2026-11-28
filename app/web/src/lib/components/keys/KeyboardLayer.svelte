<script lang="ts">
	import { afterNavigate, goto } from '$app/navigation';
	import { tick } from 'svelte';
	import { recent, visitKind } from '#lib/recent.svelte.ts';
	import { page } from '$app/state';
	import { datasetsHref } from '#lib/datasets.ts';
	import { resolve } from '$app/paths';
	import { keyboard, layerOpen, typing } from '#lib/keys.svelte.ts';
	import { modelsHref } from '#lib/models.ts';
	import { storageHref } from '#lib/storage.ts';
	import { entityHref, orgHref } from '#lib/workspace/url.ts';
	import CommandPalette from './CommandPalette.svelte';
	import ShortcutSheet from './ShortcutSheet.svelte';

	/** Hides or shows the sidebar ([). */
	let { ontogglesidebar }: { ontogglesidebar?: () => void } = $props();

	let palette = $state(false);
	/** Where the palette opens: inside Filter or Sort (F, Shift+S), else at its top. */
	let start = $state<'filter' | 'sort' | null>(null);
	$effect(() => {
		keyboard.palette = (at) => {
			start = at ?? null;
			palette = true;
		};
		return () => {
			keyboard.palette = null;
		};
	});
	let sheet = $state(false);
	/** "g" was pressed: the next key says where to go. */
	let going = false;
	let timer: ReturnType<typeof setTimeout> | undefined;

	const org = $derived(
		page.data.shell?.access === 'public' ? null : (page.data.shell?.org ?? null)
	);

	// Each page worth going back to joins Recent, named by its title's first part ("mix-w0.8 ·
	// pretrain-350m · Flock" reads as the run, in its project).
	afterNavigate(async ({ to }) => {
		const kind = visitKind(to?.route.id ?? null);
		if (!kind || !to) return;
		await tick();
		const [label, detail] = document.title.split(' · ');
		if (!label || label === 'Flock') return;
		recent.record({
			href: to.url.pathname + (kind === 'run' ? '' : to.url.search),
			label,
			kind,
			detail: detail && detail !== 'Flock' ? detail : undefined
		});
	});

	function destination(key: string): string | null {
		if (!org) return null;
		if (key === 'h') return resolve('/(app)');
		if (key === 'r') return entityHref(org);
		// Settings on Linear's key, so S is Storage's beside D and M.
		if (key === ',') return orgHref(org);
		if (!page.data.shell?.s3) return null;
		if (key === 'd') return datasetsHref(org);
		if (key === 'm') return modelsHref(org);
		if (key === 's') return storageHref(org);
		return null;
	}

	function onkeydown(e: KeyboardEvent) {
		if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === 'k') {
			e.preventDefault();
			if (keyboard.search && !palette) keyboard.search();
			else palette = !palette;
			return;
		}
		if (e.defaultPrevented || typing(e) || layerOpen()) return;
		if (going) {
			going = false;
			clearTimeout(timer);
			const to = destination(e.key.toLowerCase());
			if (to) {
				e.preventDefault();
				// eslint-disable-next-line svelte/no-navigation-without-resolve -- built with resolve()
				void goto(to);
			}
			return;
		}
		if (e.key === '?') {
			e.preventDefault();
			sheet = true;
		} else if (e.key === '[' && ontogglesidebar) {
			e.preventDefault();
			ontogglesidebar();
		} else if (e.key === 'g') {
			going = true;
			timer = setTimeout(() => (going = false), 1200);
		}
	}
</script>

<svelte:window {onkeydown} />
<CommandPalette bind:open={palette} bind:start />
<ShortcutSheet bind:open={sheet} />
