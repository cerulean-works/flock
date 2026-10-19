<script lang="ts">
	interface Props {
		/** Run color; omitted for the "all runs" toggle. */
		color?: string;
		shown: boolean;
		/** Some but not all runs shown (header toggle). */
		partial?: boolean;
		label: string;
		onclick: () => void;
	}

	let { color, shown, partial = false, label, onclick }: Props = $props();
</script>

<!-- A run's color doubles as its visibility toggle: filled when plotted, a ring when hidden. -->
<button
	class="eye -mx-1 grid size-[22px] flex-none place-items-center rounded-full hover:bg-muted"
	class:on={shown}
	style:--run={color ?? 'var(--foreground)'}
	{onclick}
	aria-label={label}
	title={label}
	aria-pressed={shown}
>
	<span
		class={[
			'size-3 rounded-full border-2 border-(--run) transition-colors',
			shown && 'bg-(--run)',
			partial && 'bg-[linear-gradient(90deg,var(--run)_50%,transparent_50%)]'
		]}
	></span>
</button>
