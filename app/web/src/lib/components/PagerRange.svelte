<script lang="ts">
	interface Props {
		/** The first and last rows shown, from 1. */
		from: number;
		to: number;
		/** The most rows there may be: sets the room the range keeps, so the arrows never move. */
		most: number;
	}

	let { from, to, most }: Props = $props();

	const fmt = (n: number) => n.toLocaleString('en-US');
	// Digits are one width (the pill's tabular figures): the widest range has the most digits.
	const widest = $derived(fmt(Number('8'.repeat(String(Math.max(1, most)).length))));
</script>

<!-- The range over an invisible copy of its widest form: the pill keeps one width as the pages
     turn, and the range sits against its "of N". -->
<span class="inline-grid text-right">
	<span class="col-start-1 row-start-1">{fmt(from)}–{fmt(to)}</span>
	<!-- Drawn by CSS, so the copy isn't in the page's text. -->
	<span
		class="invisible col-start-1 row-start-1 before:content-[attr(data-widest)]"
		data-widest="{widest}–{widest}"
		aria-hidden="true"
	></span>
</span>
