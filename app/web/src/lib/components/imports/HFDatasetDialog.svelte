<script lang="ts">
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Checkbox } from '#lib/components/ui/checkbox/index.js';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { pathError } from '#lib/datasets.ts';
	import {
		HFError,
		type SplitChoice,
		chosenConfigs,
		chosenSize,
		datasetCommand,
		defaultFolder,
		hfApi,
		hfCall,
		repoError
	} from '#lib/hf.ts';
	import type { HFDatasetPreview, HFImport } from '#lib/types.ts';
	import { bytes } from '#lib/utils.ts';
	import CopyBlock from '../CopyBlock.svelte';
	import FormError from '../FormError.svelte';

	// Asks for a dataset on the Hub, then the configs and splits to import and the folder to
	// write to (D124, D129).
	interface Props {
		open: boolean;
		org: string;
		/** The folder shown, where the dataset lands by default. */
		folder: string;
		onstarted: (job: HFImport) => void;
	}

	let { open = $bindable(), org, folder, onstarted }: Props = $props();

	type Config = HFDatasetPreview['configs'][number];

	let repo = $state('');
	let token = $state('');
	let preview = $state<HFDatasetPreview | null>(null);
	let choice = $state<SplitChoice>({});
	let dest = $state('');
	let error = $state<string | null>(null);
	let command = $state<string | null>(null);
	let pending = $state(false);

	const configs = $derived(preview?.configs ?? []);
	const several = $derived(configs.length > 1);
	const chosen = $derived(chosenConfigs(configs, choice));
	const empty = $derived(!Object.keys(chosen).length);
	const size = $derived(chosenSize(configs, choice));
	const tooLarge = $derived(!!preview && size > preview.max_bytes);
	const shownCommand = $derived(
		command ??
			(tooLarge && !empty ? datasetCommand(org, preview?.repo ?? repo, dest.trim(), chosen) : null)
	);

	const allSplits = (config: Config) => config.splits?.map((s) => s.name) ?? null;
	const picked = (config: Config): string[] => choice[config.name] ?? [];
	const whole = (config: Config) =>
		config.name in choice &&
		(choice[config.name] === null || picked(config).length === (config.splits?.length ?? 0));

	function setConfig(config: Config, on: boolean) {
		const next = { ...choice };
		if (on) next[config.name] = allSplits(config);
		else delete next[config.name];
		choice = next;
		command = null;
	}

	function setSplit(config: Config, split: string, on: boolean) {
		const was = picked(config);
		const names = (allSplits(config) ?? []).filter((n) => (n === split ? on : was.includes(n)));
		const next = { ...choice };
		if (names.length) next[config.name] = names;
		else delete next[config.name];
		choice = next;
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
			const first = found.configs.find((c) => c.name === found.default_config) ?? found.configs[0];
			choice = first ? { [first.name]: allSplits(first) } : {};
			dest = defaultFolder(folder, target);
		} catch (e) {
			error = (e as Error).message;
		} finally {
			pending = false;
		}
	}

	async function start() {
		if (!preview || empty) return;
		const target = dest.trim();
		error = pathError(target);
		if (error) return;
		pending = true;
		command = null;
		try {
			const job = await hfCall<HFImport>(hfApi(org), {
				method: 'POST',
				body: {
					kind: 'dataset',
					repo: preview.repo,
					configs: chosen,
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
		choice = {};
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
				<fieldset class="grid gap-2">
					<legend class="mb-2 text-sm font-medium"
						>{several ? 'Configs and splits' : 'Splits'}</legend
					>
					{#each configs as config (config.name)}
						{#if several}
							<div class="flex items-center gap-2.5">
								<Checkbox
									id="hf-config-{config.name}"
									checked={whole(config)}
									indeterminate={config.name in choice && !whole(config)}
									onCheckedChange={(on) => setConfig(config, on)}
								/>
								<Label for="hf-config-{config.name}" class="flex-1">{config.name}</Label>
								<span class="text-[12.5px] text-muted-foreground tabular-nums">
									{config.size ? bytes(config.size) : ''}
								</span>
							</div>
						{/if}
						{#each config.splits ?? [] as split (split.name)}
							<div class={['flex items-center gap-2.5', several && 'pl-6.5']}>
								<Checkbox
									id="hf-split-{config.name}-{split.name}"
									aria-label={several ? `${split.name} of ${config.name}` : undefined}
									checked={picked(config).includes(split.name)}
									onCheckedChange={(on) => setSplit(config, split.name, on)}
								/>
								<Label for="hf-split-{config.name}-{split.name}" class="flex-1 font-normal">
									{split.name}
								</Label>
								<span class="text-[12.5px] text-muted-foreground tabular-nums">
									{split.size === null ? '' : bytes(split.size)}
								</span>
							</div>
						{/each}
					{/each}
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
				<Button type="submit" disabled={pending || (!!preview && (tooLarge || empty))}>
					{#if pending}<LoaderCircleIcon class="animate-spin" />{/if}
					{preview ? 'Import' : 'Continue'}
				</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
