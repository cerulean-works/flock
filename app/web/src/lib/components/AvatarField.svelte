<script lang="ts">
	import { enhance } from '$app/forms';
	import Avatar from '#lib/components/Avatar.svelte';
	import FormError from '#lib/components/FormError.svelte';
	import OrgMark from '#lib/components/shell/OrgMark.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import { AVATAR_TYPES, avatarFileError } from '#lib/utils.ts';

	interface Props {
		/** A person's (round) or an organization's (square) picture. */
		kind: 'user' | 'org';
		name: string;
		src: string | null;
		/** Offer "Use Gravatar" (users only); false once the picture is already from there. */
		gravatar?: boolean;
	}

	let { kind, name, src, gravatar = false }: Props = $props();

	let input = $state<HTMLInputElement>();
	let form = $state<HTMLFormElement>();

	const uploading = new Mutation({
		validate: (data) => avatarFileError(data.get('avatar')),
		success: () => 'Picture updated',
		done: () => form?.reset()
	});
	const removing = new Mutation({ success: () => 'Picture removed', toastErrors: true });
	const fetching = new Mutation({ success: () => 'Picture from Gravatar', toastErrors: true });

	const busy = $derived(uploading.pending || removing.pending || fetching.pending);
	// A failed upload is reset too, so picking the same file again tries again.
	$effect(() => {
		if (uploading.error) form?.reset();
	});
</script>

<div class="flex items-center gap-4">
	{#if kind === 'user'}
		<Avatar {name} {src} class="size-16 text-[22px]" />
	{:else}
		<OrgMark {name} {src} class="size-16 text-[30px]" />
	{/if}
	<div class="grid min-w-0 gap-2">
		<div class="flex flex-wrap gap-2">
			<!-- Picking a file sends it: there is nothing else to fill in. -->
			<form
				method="POST"
				action="?/avatar"
				enctype="multipart/form-data"
				bind:this={form}
				use:enhance={uploading.enhance}
			>
				<input
					bind:this={input}
					type="file"
					name="avatar"
					accept={AVATAR_TYPES.join(',')}
					class="sr-only"
					tabindex="-1"
					aria-hidden="true"
					onchange={() => input?.files?.length && form?.requestSubmit()}
				/>
				<Button variant="outline" disabled={busy} onclick={() => input?.click()}>
					{uploading.pending ? 'Uploading…' : src ? 'Change picture' : 'Upload a picture'}
				</Button>
			</form>
			{#if src}
				<form method="POST" action="?/removeAvatar" use:enhance={removing.enhance}>
					<Button type="submit" variant="ghost" disabled={busy}>Remove</Button>
				</form>
			{/if}
			{#if gravatar}
				<form method="POST" action="?/gravatar" use:enhance={fetching.enhance}>
					<Button type="submit" variant="ghost" disabled={busy}>
						{fetching.pending ? 'Fetching…' : 'Use Gravatar'}
					</Button>
				</form>
			{/if}
		</div>
		<p class="text-[13px] text-muted-foreground">PNG, JPEG, GIF or WebP, up to 1 MB.</p>
		<FormError error={uploading.error} />
	</div>
</div>
