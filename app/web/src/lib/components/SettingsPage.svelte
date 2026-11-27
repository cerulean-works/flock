<script lang="ts">
	import type { Snippet } from 'svelte';
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import type { ResolvedPathname } from '$app/types';
	import Page from '#lib/components/Page.svelte';
	import PageHeader from '#lib/components/PageHeader.svelte';
	import type { Crumb } from '#lib/storage.ts';

	interface Props {
		/** The section, the trail's last step. */
		title: string;
		/** The steps before it: Settings, Account or Super admin. */
		trail: Crumb[];
		/** Lead the trail with the org (an org's settings). */
		org?: boolean;
		/**
		 * Where an old one-page link's `#anchor` now lives: followed once on arrival, when it's
		 * another section's page.
		 */
		follow?: (hash: string) => ResolvedPathname | null;
		children: Snippet;
	}

	let { title, trail, org = false, follow, children }: Props = $props();

	onMount(() => {
		const to = location.hash && follow?.(location.hash);
		// eslint-disable-next-line svelte/no-navigation-without-resolve -- resolved by its type
		if (to && to !== location.pathname) void goto(to, { replaceState: true });
	});
</script>

<!--
	A settings page (G1, G2): the trail, then one left-aligned 640px column whose sections share
	one heading size. The sidebar lists the other sections.
-->
<Page width="full">
	<PageHeader {title} {trail} {org} />
	<main class="min-h-0 flex-1 overflow-y-auto px-5 pb-16">
		<div class="max-w-160 pt-8">{@render children()}</div>
	</main>
</Page>
