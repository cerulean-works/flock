<script lang="ts">
	import logo from '#lib/assets/brand/logo.png';

	let { class: className = 'size-7' }: { class?: string } = $props();
</script>

<!-- The supplied gradient disc. The PNG has a square matte; this crop (from the Cerulean
     website's LogoMark) keeps only the circle. -->
<span
	class={['relative inline-block flex-none overflow-hidden rounded-full', className]}
	aria-hidden="true"
>
	<img
		src={logo}
		alt=""
		width="256"
		height="250"
		class="absolute top-[-9.525%] left-[-10.975%] h-[119.05%] w-[121.95%] max-w-none"
	/>
</span>
