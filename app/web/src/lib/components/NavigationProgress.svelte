<script lang="ts">
	import { navigating } from '$app/state';

	/** Navigations faster than this never show the bar, so quick clicks don't flash it. */
	const DELAY = 120;
	/** How often the bar creeps forward while a page loads. */
	const TRICKLE = 250;

	let progress = $state(0);
	let visible = $state(false);
	let hide: ReturnType<typeof setTimeout> | undefined;

	// A boolean, so the effect only reruns when loading starts or ends, not on every update of
	// the navigation object.
	const loading = $derived(navigating.to !== null);

	$effect(() => {
		if (!loading) return;
		let trickle: ReturnType<typeof setInterval> | undefined;
		const start = setTimeout(() => {
			clearTimeout(hide);
			visible = true;
			progress = 0.1;
			// Ease toward 90% without reaching it: the real end comes when the page arrives.
			trickle = setInterval(() => (progress += (0.9 - progress) * 0.12), TRICKLE);
		}, DELAY);
		return () => {
			clearTimeout(start);
			clearInterval(trickle);
			if (!visible) return;
			progress = 1;
			hide = setTimeout(() => {
				visible = false;
				// Reset after the fade, so the bar doesn't visibly shrink back.
				hide = setTimeout(() => (progress = 0), 200);
			}, 200);
		};
	});
</script>

<!-- A thin bar across the top of the window while a page loads. -->
<div
	class={[
		'pointer-events-none fixed inset-x-0 top-0 z-[100] h-0.5 transition-opacity duration-200',
		visible ? 'opacity-100' : 'opacity-0'
	]}
	aria-hidden="true"
>
	<div
		class="h-full origin-left bg-primary shadow-[0_0_8px_var(--color-primary)] transition-transform duration-200 ease-out motion-reduce:transition-none"
		style:transform="scaleX({progress})"
	></div>
</div>
