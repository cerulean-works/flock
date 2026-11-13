<script lang="ts">
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Checkbox } from '#lib/components/ui/checkbox/index.js';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import * as Select from '#lib/components/ui/select/index.js';
	import { pathError } from '#lib/datasets.ts';
	import { HFError, datasetCommand, defaultFolder, hfApi, hfCall, repoError } from '#lib/hf.ts';
	import type { HFDatasetPreview, HFImport } from '#lib/types.ts';
	import { bytes } from '#lib/utils.ts';
	import CopyBlock from '../CopyBlock.svelte';
	import FormError from '../FormError.svelte';

	// Asks for a dataset on the Hub, then its config, splits and the folder to write to (D124).
	interface Props {
		open: boolean;
		org: string;
		/** The folder shown, where the dataset lands by default. */
		folder: string;
		onstarted: (job: HFImport) => void;
	}

	let { open = $bindable(), org, folder, onstarted }: Props = $props();

	let repo = $state('');
	let token = $state('');
	let preview = $state<HFDatasetPreview | null>(null);
	let config = $state('');
	let chosen = $state<string[]>([]);
	let dest = $state('');
	let error = $state<string | null>(null);
	let command = $state<string | null>(null);
	let pending = $state(false);

	const current = $derived(preview?.configs.find((c) => c.name === config) ?? null);
	const splits = $derived(current?.splits ?? null);
	const all = $derived(!splits || chosen.length === splits.length);
	const size = $derived(
		splits
			? splits.filter((s) => chosen.includes(s.name)).reduce((n, s) => n + (s.size ?? 0), 0)
			: (current?.size ?? 0)
	);
	const tooLarge = $derived(!!preview && size > preview.max_bytes);
	const shownCommand = $derived(
		command ??
			(tooLarge
				? datasetCommand(org, preview?.repo ?? repo, dest.trim(), config, all ? null : chosen)
				: null)
	);

	function choose(name: string) {
		config = name;
		chosen = preview?.configs.find((c) => c.name === name)?.splits?.map((s) => s.name) ?? [];
		command = null;
	}

	async function lookUp() {
		const target = repo.trim();
		error = repoError(target);
		if (error) return;
		pending = true;
		try {
			const found = await hfCall<HFDatasetPreview>(
				hfApi(org, 'preview', { kind: 'dataset', repo: target }),
				{ token: token.trim() || undefined }
			);
			preview = found;
			choose(found.default_config ?? found.configs[0]?.name ?? '');
			dest = defaultFolder(folder, target);
		} catch (e) {
			error = (e as Error).message;
		} finally {
			pending = false;
		}
	}

	async function start() {
		if (!preview) return;
		const target = dest.trim();
		error = pathError(target) ?? (splits && !chosen.length ? 'Choose at least one split' : null);
		if (error) return;
		pending = true;
		command = null;
		try {
			const job = await hfCall<HFImport>(hfApi(org), {
				method: 'POST',
				body: {
					kind: 'dataset',
					repo: preview.repo,
					config: config || null,
					splits: all ? null : chosen,
					dest: target,
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
		token = '';
		preview = null;
		config = '';
		chosen = [];
		dest = '';
		error = null;
		command = null;
	}
</script>

<Dialog.Root bind:open onOpenChangeComplete={(o) => !o && reset()}>
	<Dialog.Content class="sm:max-w-lg">
		<Dialog.Header>
			<Dialog.Title>Import a dataset from Hugging Face</Dialog.Title>
			<Dialog.Description>Each split becomes a Lance dataset in flock-datasets.</Dialog.Description>
		</Dialog.Header>
		<form
			class="grid gap-4"
			onsubmit={(e) => {
				e.preventDefault();
				void (preview ? start() : lookUp());
			}}
		>
			<div class="grid gap-2">
				<Label for="hf-dataset-repo">Repository</Label>
				<div class="flex gap-2">
					<Input
						id="hf-dataset-repo"
						class="font-mono text-[13px]"
						placeholder="HuggingFaceFW/fineweb-edu"
						bind:value={repo}
						disabled={pending || !!preview}
						autocomplete="off"
						spellcheck="false"
					/>
					{#if preview}
						<Button type="button" variant="outline" disabled={pending} onclick={() => reset()}>
							Change
						</Button>
					{/if}
				</div>
			</div>
			{#if !preview}
				<div class="grid gap-2">
					<Label for="hf-dataset-token">Access token</Label>
					<Input
						id="hf-dataset-token"
						type="password"
						bind:value={token}
						disabled={pending}
						autocomplete="off"
					/>
					<p class="text-[12.5px] text-muted-foreground">
						Only for gated and private datasets. Flock doesn’t keep it.
					</p>
				</div>
			{:else}
				{#if preview.configs.length > 1}
					<div class="grid gap-2">
						<Label for="hf-dataset-config">Config</Label>
						<Select.Root type="single" value={config} onValueChange={choose}>
							<Select.Trigger id="hf-dataset-config" class="w-full">{config}</Select.Trigger>
							<Select.Content>
								{#each preview.configs as c (c.name)}
									<Select.Item value={c.name} label={c.name}>
										{c.name}
										{#if c.size}
											<span class="ml-auto text-xs text-muted-foreground tabular-nums">
												{bytes(c.size)}
											</span>
										{/if}
									</Select.Item>
								{/each}
							</Select.Content>
						</Select.Root>
					</div>
				{/if}
				<fieldset class="grid gap-2">
					<legend class="mb-2 text-sm font-medium">Splits</legend>
					{#if splits}
						{#each splits as split (split.name)}
							<div class="flex items-center gap-2.5">
								<Checkbox
									id="hf-split-{split.name}"
									checked={chosen.includes(split.name)}
									onCheckedChange={(on) =>
										(chosen = on
											? splits
													.map((s) => s.name)
													.filter((n) => n === split.name || chosen.includes(n))
											: chosen.filter((n) => n !== split.name))}
								/>
								<Label for="hf-split-{split.name}" class="flex-1 font-normal">{split.name}</Label>
								<span class="text-[12.5px] text-muted-foreground tabular-nums">
									{split.size === null ? '' : bytes(split.size)}
								</span>
							</div>
						{/each}
					{:else}
						<p class="text-[13px] text-muted-foreground">Every split of {config} is imported.</p>
					{/if}
				</fieldset>
				<div class="grid gap-2">
					<Label for="hf-dataset-dest">Folder</Label>
					<Input
						id="hf-dataset-dest"
						class="font-mono text-[13px]"
						bind:value={dest}
						disabled={pending}
						autocomplete="off"
						spellcheck="false"
					/>
				</div>
				{#if tooLarge && !command}
					<p class="text-[13px]">
						This is {bytes(size)}, more than the {bytes(preview.max_bytes)} this server imports. Run it
						with flock instead.
					</p>
				{/if}
			{/if}
			<FormError {error} />
			{#if shownCommand}<CopyBlock code={shownCommand} />{/if}
			<Dialog.Footer>
				<Button type="button" variant="outline" disabled={pending} onclick={() => (open = false)}>
					Cancel
				</Button>
				<Button type="submit" disabled={pending || tooLarge}>
					{#if pending}<LoaderCircleIcon class="animate-spin" />{/if}
					{preview ? 'Import' : 'Continue'}
				</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
