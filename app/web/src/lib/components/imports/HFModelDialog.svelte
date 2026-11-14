<script lang="ts">
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Checkbox } from '#lib/components/ui/checkbox/index.js';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { HFError, defaultModelName, hfApi, hfCall, repoError } from '#lib/hf.ts';
	import { tagError } from '#lib/models.ts';
	import type { HFImport } from '#lib/types.ts';
	import { parseTags } from '#lib/utils.ts';
	import CopyBlock from '../CopyBlock.svelte';
	import FormError from '../FormError.svelte';

	// Asks for a model on the Hub and the version to make of it (D124). The API checks the rest
	// before it starts.
	interface Props {
		open: boolean;
		org: string;
		onstarted: (job: HFImport) => void;
	}

	let { open = $bindable(), org, onstarted }: Props = $props();

	let repo = $state('');
	let revision = $state('main');
	// Follows the repository until it's edited.
	let named = $state<string | null>(null);
	const name = $derived(named ?? defaultModelName(repo));
	let tags = $state('');
	let primary = $state(false);
	let token = $state('');
	let error = $state<string | null>(null);
	let command = $state<string | null>(null);
	let pending = $state(false);

	async function start() {
		const target = repo.trim();
		const tagList = parseTags(tags);
		error =
			repoError(target) ??
			(name ? null : 'Enter a model name') ??
			tagList.map(tagError).find((e) => e !== null) ??
			null;
		if (error) return;
		pending = true;
		command = null;
		try {
			const job = await hfCall<HFImport>(hfApi(org), {
				method: 'POST',
				body: {
					kind: 'model',
					repo: target,
					revision: revision.trim() || 'main',
					name,
					tags: tagList,
					primary,
					token: token.trim() || null
				}
			});
			onstarted(job);
			open = false;
		} catch (e) {
			error = (e as Error).message;
			command = e instanceof HFError ? e.command : null;
		} finally {
			pending = false;
		}
	}

	function reset() {
		repo = '';
		revision = 'main';
		named = null;
		tags = '';
		primary = false;
		token = '';
		error = null;
		command = null;
	}
</script>

<Dialog.Root bind:open onOpenChangeComplete={(o) => !o && reset()}>
	<Dialog.Content class="sm:max-w-lg">
		<Dialog.Header>
			<Dialog.Title>Import a model from Hugging Face</Dialog.Title>
			<Dialog.Description>
				The model becomes a new version in Flock’s format, ready to train.
			</Dialog.Description>
		</Dialog.Header>
		<form
			class="grid gap-4"
			onsubmit={(e) => {
				e.preventDefault();
				void start();
			}}
		>
			<div class="grid gap-2">
				<Label for="hf-model-repo">Repository</Label>
				<Input
					id="hf-model-repo"
					class="font-mono text-[13px]"
					placeholder="Qwen/Qwen3-0.6B"
					bind:value={repo}
					disabled={pending}
					autocomplete="off"
					spellcheck="false"
				/>
			</div>
			<div class="grid gap-4 sm:grid-cols-2">
				<div class="grid gap-2">
					<Label for="hf-model-revision">Revision</Label>
					<Input
						id="hf-model-revision"
						class="font-mono text-[13px]"
						bind:value={revision}
						disabled={pending}
						autocomplete="off"
						spellcheck="false"
					/>
				</div>
				<div class="grid gap-2">
					<Label for="hf-model-name">Model name</Label>
					<Input
						id="hf-model-name"
						class="font-mono text-[13px]"
						value={name}
						oninput={(e) => (named = e.currentTarget.value.trim())}
						disabled={pending}
						autocomplete="off"
						spellcheck="false"
					/>
				</div>
			</div>
			<div class="grid gap-2">
				<Label for="hf-model-tags">Tags</Label>
				<Input
					id="hf-model-tags"
					bind:value={tags}
					disabled={pending}
					autocomplete="off"
					spellcheck="false"
				/>
			</div>
			<div class="flex items-center gap-2.5">
				<Checkbox id="hf-model-primary" bind:checked={primary} disabled={pending} />
				<Label for="hf-model-primary" class="font-normal">Make it the primary version</Label>
			</div>
			<div class="grid gap-2">
				<Label for="hf-model-token">Access token</Label>
				<Input
					id="hf-model-token"
					type="password"
					bind:value={token}
					disabled={pending}
					autocomplete="off"
				/>
				<p class="text-[12.5px] text-muted-foreground">
					Only for gated and private models. Flock doesn’t keep it.
				</p>
			</div>
			<FormError {error} />
			{#if command}<CopyBlock code={command} />{/if}
			<Dialog.Footer>
				<Button type="button" variant="outline" disabled={pending} onclick={() => (open = false)}>
					Cancel
				</Button>
				<Button type="submit" disabled={pending}>
					{#if pending}<LoaderCircleIcon class="animate-spin" />{/if}
					Import
				</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
