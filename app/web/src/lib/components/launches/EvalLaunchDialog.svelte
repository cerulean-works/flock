<script lang="ts">
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Checkbox } from '#lib/components/ui/checkbox/index.js';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { datasetCall } from '#lib/datasets.ts';
	import { repoError } from '#lib/hf.ts';
	import { launchesApi } from '#lib/launches.svelte.ts';
	import type { EvalCatalogue, EvalLaunch } from '#lib/types.ts';
	import { money } from '#lib/utils.ts';
	import FormError from '../FormError.svelte';

	// Asks for a public model on the Hub and the suites and tasks to score it on (026). The
	// instrument (BF16, the bank's batch size) is fixed by the server, so it is stated, not asked.
	interface Props {
		open: boolean;
		org: string;
		project: string;
		/** What may be scored; null until the server answers. */
		catalogue: EvalCatalogue | null;
		/** Why the catalogue couldn't be read. */
		catalogueError: string | null;
		onstarted: (job: EvalLaunch) => void;
	}

	let { open = $bindable(), org, project, catalogue, catalogueError, onstarted }: Props = $props();

	let model = $state('');
	let revision = $state('main');
	let filter = $state('');
	let suites = $state<string[]>([]);
	let tasks = $state<string[]>([]);
	let addBos = $state(false);
	let error = $state<string | null>(null);
	let pending = $state(false);

	const matches = (name: string) => name.toLowerCase().includes(filter.trim().toLowerCase());
	// Each list's choices, kept while the filter hides some of them.
	const groups = $derived([
		{
			label: 'Suites',
			names: catalogue?.suites.filter(matches) ?? [],
			chosen: suites,
			choose: (next: string[]) => (suites = next)
		},
		{
			label: 'Tasks',
			names: catalogue?.tasks.filter(matches) ?? [],
			chosen: tasks,
			choose: (next: string[]) => (tasks = next)
		}
	]);

	async function launch() {
		const target = model.trim();
		error = repoError(target) ?? (suites.length || tasks.length ? null : 'Pick a suite or a task');
		if (error) return;
		pending = true;
		try {
			const job = await datasetCall<EvalLaunch>(launchesApi(org), {
				method: 'POST',
				body: {
					model: target,
					revision: revision.trim() || 'main',
					suites,
					tasks,
					add_bos: addBos,
					project
				}
			});
			onstarted(job);
			open = false;
		} catch (e) {
			error = (e as Error).message;
		} finally {
			pending = false;
		}
	}

	function reset() {
		model = '';
		revision = 'main';
		filter = '';
		suites = [];
		tasks = [];
		addBos = false;
		error = null;
	}
</script>

<Dialog.Root bind:open onOpenChangeComplete={(o) => !o && reset()}>
	<Dialog.Content class="sm:max-w-lg">
		<Dialog.Header>
			<Dialog.Title>Launch an eval</Dialog.Title>
			<Dialog.Description>
				Scores a public model from Hugging Face and logs it as a run of {project}.
			</Dialog.Description>
		</Dialog.Header>
		{#if !catalogue}
			<FormError error={catalogueError} />
			{#if !catalogueError}
				<p class="flex items-center gap-2 text-[13px] text-muted-foreground" role="status">
					<LoaderCircleIcon class="size-4 animate-spin" />
					Reading the suites and tasks. The first time can take a minute.
				</p>
			{/if}
		{:else}
			<form
				class="grid gap-4"
				onsubmit={(e) => {
					e.preventDefault();
					void launch();
				}}
			>
				<div class="grid gap-4 sm:grid-cols-[1fr_10rem]">
					<div class="grid gap-2">
						<Label for="launch-model">Model repository</Label>
						<Input
							id="launch-model"
							class="font-mono text-[13px]"
							placeholder="e.g., allenai/OLMo-2-0425-1B"
							bind:value={model}
							disabled={pending}
							autocomplete="off"
							spellcheck="false"
						/>
					</div>
					<div class="grid gap-2">
						<Label for="launch-revision">Revision</Label>
						<Input
							id="launch-revision"
							class="font-mono text-[13px]"
							placeholder="e.g., main"
							bind:value={revision}
							disabled={pending}
							autocomplete="off"
							spellcheck="false"
						/>
					</div>
				</div>
				<div class="grid gap-2">
					<Label for="launch-filter">Suites and tasks</Label>
					<Input
						id="launch-filter"
						placeholder="Filter"
						bind:value={filter}
						disabled={pending}
						autocomplete="off"
						spellcheck="false"
					/>
					<div class="grid max-h-60 gap-3 overflow-y-auto rounded-md border px-3 py-2.5">
						{#each groups as group (group.label)}
							<fieldset class="grid gap-2">
								<legend class="mb-1 text-[12.5px] text-muted-foreground">{group.label}</legend>
								{#each group.names as name (name)}
									{@const id = `launch-${group.label}-${name}`}
									<div class="flex items-center gap-2.5">
										<Checkbox
											{id}
											checked={group.chosen.includes(name)}
											onCheckedChange={(c) =>
												group.choose(
													c ? [...group.chosen, name] : group.chosen.filter((n) => n !== name)
												)}
											disabled={pending}
										/>
										<Label for={id} class="font-mono text-[13px] font-normal">{name}</Label>
									</div>
								{:else}
									<p class="text-[13px] text-faint">None</p>
								{/each}
							</fieldset>
						{/each}
					</div>
				</div>
				<div class="flex items-center gap-2.5">
					<Checkbox id="launch-bos" bind:checked={addBos} disabled={pending} />
					<Label for="launch-bos" class="font-normal">Add BOS (Gemma)</Label>
				</div>
				<p class="text-[12.5px] text-muted-foreground">
					Scored in BF16 on a Modal H100, at most {money(catalogue.max_usd, 'USD')} per launch. The results
					are labelled as H100 runs: they aren’t comparable with the H200 bank until a parity check passes.
				</p>
				<FormError {error} />
				<Dialog.Footer>
					<Button type="button" variant="outline" disabled={pending} onclick={() => (open = false)}>
						Cancel
					</Button>
					<Button type="submit" disabled={pending}>
						{#if pending}<LoaderCircleIcon class="animate-spin" />{/if}
						Launch
					</Button>
				</Dialog.Footer>
			</form>
		{/if}
	</Dialog.Content>
</Dialog.Root>
