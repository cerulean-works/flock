<script lang="ts">
	import { untrack, type Snippet } from 'svelte';
	import { afterNavigate } from '$app/navigation';
	import Page from '#lib/components/Page.svelte';
	import PageHeader from '#lib/components/PageHeader.svelte';
	import { jumpTo, scrollspy } from '#lib/settings-toc.svelte.ts';
	import type { Crumb } from '#lib/storage.ts';

	interface Props {
		/** The area: Settings, Account or Super admin, the trail's last step. */
		title: string;
		/** Steps before it. */
		trail?: Crumb[];
		/** Lead the trail with the org (an org's settings). */
		org?: boolean;
		/** Where an `#anchor` points, when it isn't an element's own id (an old link's). */
		target?: (hash: string) => string;
		/** At the header's right: the page's one action (New organization). */
		actions?: Snippet;
		children: Snippet;
	}

	let {
		title,
		trail = [],
		org = false,
		target = (h) => h.slice(1),
		actions,
		children
	}: Props = $props();

	// Arriving with an `#anchor` (a link to a section, or an old one-page link), or the anchor
	// changing on this page: straight there, its section marked.
	const arrive = () => {
		if (location.hash) jumpTo(decodeURIComponent(target(location.hash)), { smooth: false });
	};
	afterNavigate(arrive);
</script>

<svelte:window onhashchange={arrive} />

<!--
	A settings page (G1, G2): the trail naming the area, then one left-aligned 640px column of
	its sections, each headed, in the order the sidebar lists them as its table of contents.
-->
<Page width="full">
	<PageHeader {title} {trail} {org} {actions} />
	<main
		class="relative min-h-0 flex-1 overflow-y-auto px-5 pb-[40vh]"
		{@attach (node) => untrack(() => scrollspy(node))}
	>
		<div class="max-w-160 pt-8">{@render children()}</div>
	</main>
</Page>
