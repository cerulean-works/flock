<script lang="ts">
	import ActivityIcon from '@lucide/svelte/icons/activity';
	import BoxIcon from '@lucide/svelte/icons/box';
	import DatabaseIcon from '@lucide/svelte/icons/database';
	import { datasetsHref } from '#lib/datasets.ts';
	import { modelsHref } from '#lib/models.ts';
	import { projectHref } from '#lib/workspace/url.ts';

	interface Props {
		entity: string;
		project: string;
		/** The tab shown. */
		current: 'runs' | 'datasets' | 'models';
	}

	let { entity, project, current }: Props = $props();

	const scope = $derived({ org: entity, project });
	const items = $derived([
		{ value: 'runs', label: 'Runs', href: projectHref(entity, project), Icon: ActivityIcon },
		{ value: 'datasets', label: 'Datasets', href: datasetsHref(scope), Icon: DatabaseIcon },
		{ value: 'models', label: 'Models', href: modelsHref(scope), Icon: BoxIcon }
	]);
</script>

<!-- A project's runs, datasets and models (D169). The pages show it to the org's members when
     Flock has S3 storage, since outsiders never see a project's datasets and models (D165). -->
<nav class="flex items-center gap-0.5" aria-label="Project">
	{#each items as { value, label, href, Icon } (value)}
		<a
			class={[
				'inline-flex h-7 items-center gap-1.5 rounded-md px-2 text-[13px] whitespace-nowrap',
				value === current
					? 'bg-muted font-medium text-foreground'
					: 'text-muted-foreground hover:bg-muted hover:text-foreground'
			]}
			{href}
			aria-current={value === current ? 'page' : undefined}
		>
			<Icon class="size-3.5" />
			{label}
		</a>
	{/each}
</nav>
