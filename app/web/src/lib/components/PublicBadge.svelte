<script lang="ts">
	import GlobeIcon from '@lucide/svelte/icons/globe';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { cn } from '#lib/utils.js';

	interface Props {
		/** What anyone with the link can see of it, for its hover text. */
		what?: string;
		/** Extra classes, e.g. to match the height of the controls beside it. */
		class?: string;
	}

	let { what = 'it', class: className }: Props = $props();
</script>

<!-- Beside a public project's or dataset's name, for members and outsiders alike (D116). -->
<Badge
	variant="outline"
	class={cn('public-badge text-muted-foreground', className)}
	title="Anyone with the link can see {what}"
>
	<GlobeIcon /> Public
</Badge>
