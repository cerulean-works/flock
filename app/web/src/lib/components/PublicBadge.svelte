<script lang="ts">
	import GlobeIcon from '@lucide/svelte/icons/globe';
	import { Badge } from '#lib/components/ui/badge/index.js';

	/** What anyone with the link can see of it, for its hover text. */
	let { what = 'it' }: { what?: string } = $props();
</script>

<!-- Beside a public project's or dataset's name, for members and outsiders alike (D116). -->
<Badge
	variant="outline"
	class="public-badge text-muted-foreground"
	title="Anyone with the link can see {what}"
>
	<GlobeIcon /> Public
</Badge>
