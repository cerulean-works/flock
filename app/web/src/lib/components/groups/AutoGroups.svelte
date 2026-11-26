<script lang="ts">
	import ArrowUpIcon from '@lucide/svelte/icons/arrow-up';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import SparklesIcon from '@lucide/svelte/icons/sparkles';
	import SquareIcon from '@lucide/svelte/icons/square';
	import { onMount, tick } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { goto, invalidate } from '$app/navigation';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import {
		GROUPS,
		GroupsError,
		classifyColumns,
		configOf,
		groupsApi,
		groupsCall,
		groupsHref,
		keysOf,
		type GroupList,
		type Proposal,
		type ProposeAnswer,
		type ProposeMessage,
		type RunRow
	} from '#lib/groups.ts';
	import { count } from '#lib/utils.ts';
	import ProposalCard from './ProposalCard.svelte';
	import {
		autoPrompts,
		createBody,
		proposalColors,
		thinkingText,
		ungroupedCount
	} from './propose.ts';

	/**
	 * The create page's Auto tab: a conversation in which the assistant proposes groups of the
	 * project's runs, each a card, and the user asks for changes, then creates the ones they keep
	 * and lands on Compare. It opens with the first proposal already on its way.
	 */

	interface Props {
		entity: string;
		project: string;
		rows: RunRow[];
		groupList: GroupList | null;
		/** Whether the viewer may use the assistant; without it the tab points to Manual. */
		available: boolean;
		/** Go to Manual, seeded with a proposal when one is given. */
		onmanual: (proposal?: Proposal, name?: string) => void;
	}

	let { entity, project, rows, groupList, available, onmanual }: Props = $props();

	type Turn =
		| { kind: 'user'; text: string }
		| { kind: 'assistant'; answer: ProposeAnswer; colors: string[] }
		| { kind: 'error'; text: string };

	let turns = $state<Turn[]>([]);
	let pending = $state(false);
	/** The assistant turned out to be off (403, or 404 from an older Flock). */
	let refused = $state(false);
	let started = $state(0);
	let now = $state(Date.now());
	let controller: AbortController | null = null;

	// The latest proposals' edits: kept out, removed, renamed; and how creating them went.
	let included = $state<Record<string, boolean>>({});
	let removed = $state<Record<string, boolean>>({});
	let names = $state<Record<string, string>>({});
	let statuses = $state<Record<string, 'creating' | 'done' | 'failed'>>({});
	let failures = $state<Record<string, string>>({});
	let creating = $state(false);
	/** Earlier proposal sets opened from their summary. */
	let expanded = $state<Record<number, boolean>>({});

	let text = $state('');
	let scroller = $state<HTMLElement>();
	let input = $state<HTMLTextAreaElement>();

	const usable = $derived(available && !refused);
	const varying = $derived(keysOf(classifyColumns(rows.map(configOf)), 'varies').length);
	const prompts = $derived(autoPrompts(rows));
	const asked = $derived(turns.some((t) => t.kind === 'user'));
	const latestIndex = $derived(turns.findLastIndex((t) => t.kind === 'assistant'));
	const latest = $derived(
		latestIndex >= 0 ? (turns[latestIndex] as Extract<Turn, { kind: 'assistant' }>) : null
	);
	const shown = $derived(latest?.answer.proposals.filter((p) => !removed[p.id]) ?? []);
	const chosen = $derived(shown.filter((p) => included[p.id] !== false));
	const ungrouped = $derived(
		latest ? ungroupedCount(latest.answer.ungrouped, latest.answer.proposals, chosen) : 0
	);
	const seconds = $derived(pending ? Math.max(0, Math.floor((now - started) / 1000)) : 0);
	const createdCount = $derived(Object.values(statuses).filter((s) => s === 'done').length);

	// The thinking line's clock.
	$effect(() => {
		if (!pending) return;
		const timer = setInterval(() => (now = Date.now()), 500);
		return () => clearInterval(timer);
	});

	// The conversation follows its newest turn.
	$effect(() => {
		void turns.length;
		void pending;
		void tick().then(() => scroller?.scrollTo({ top: scroller.scrollHeight, behavior: 'smooth' }));
	});

	onMount(() => {
		if (usable && rows.length) void propose();
		return () => controller?.abort();
	});

	/** What the latest proposals are now, as a revision sends them: renamed, without removed ones. */
	const current = () =>
		latest ? shown.map((p) => ({ ...p, name: names[p.id]?.trim() || p.name })) : null;

	const messages = (): ProposeMessage[] =>
		turns.flatMap((t): ProposeMessage[] =>
			t.kind === 'user'
				? [{ role: 'user', content: t.text }]
				: t.kind === 'assistant'
					? [{ role: 'assistant', content: t.answer.message }]
					: []
		);

	/** Ask for proposals: the first ones (an empty conversation), or a revision of the latest. */
	async function propose(request?: string) {
		if (pending) return;
		if (request) turns.push({ kind: 'user', text: request });
		// A retry drops the error it answers.
		if (turns.at(-1)?.kind === 'error') turns.pop();
		pending = true;
		started = now = Date.now();
		controller = new AbortController();
		try {
			const answer = await groupsCall<ProposeAnswer>(groupsApi(entity, project, 'propose'), {
				method: 'POST',
				body: { messages: messages(), proposals: current() },
				signal: controller.signal
			});
			const used = (groupList?.groups ?? []).map((g) => g.color);
			turns.push({
				kind: 'assistant',
				answer,
				colors: proposalColors(answer.proposals.length, used)
			});
			included = {};
			removed = {};
			names = {};
			statuses = {};
			failures = {};
		} catch (err) {
			if (err instanceof DOMException && err.name === 'AbortError') {
				turns.push({ kind: 'error', text: 'Stopped.' });
			} else if (err instanceof GroupsError && (err.status === 403 || err.status === 404)) {
				refused = true;
			} else {
				const status = err instanceof GroupsError ? err.status : 0;
				turns.push({
					kind: 'error',
					text:
						status === 502
							? 'The assistant didn’t come back with groups this time.'
							: err instanceof Error
								? err.message
								: 'Something went wrong.'
				});
			}
		} finally {
			pending = false;
			controller = null;
		}
	}

	function send(e?: Event) {
		e?.preventDefault();
		const t = text.trim();
		if (!t || pending || creating) return;
		text = '';
		void propose(t);
		void tick().then(() => input?.focus());
	}

	function onkeydown(e: KeyboardEvent) {
		if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) send(e);
	}

	/** Create the kept proposals, one after another; on success, go to Compare. */
	async function create() {
		if (!latest || creating) return;
		creating = true;
		const colors = new Map(latest.answer.proposals.map((p, i) => [p.id, latest.colors[i]]));
		for (const p of chosen) {
			if (statuses[p.id] === 'done') continue;
			statuses[p.id] = 'creating';
			try {
				await groupsCall(groupsApi(entity, project), {
					method: 'POST',
					body: createBody(p, colors.get(p.id)!, nameOf(p))
				});
				statuses[p.id] = 'done';
				delete failures[p.id];
			} catch (err) {
				statuses[p.id] = 'failed';
				failures[p.id] = err instanceof Error ? err.message : 'Flock refused it';
			}
		}
		await invalidate(GROUPS);
		creating = false;
		const failed = chosen.filter((p) => statuses[p.id] === 'failed').length;
		if (!failed) {
			toast.success(`Created ${count(chosen.length, 'group')}`);
			await goto(groupsHref(entity, project));
		}
	}

	const isLatest = (i: number) => i === latestIndex;
	/** A proposal's name as edited, else the API's (unique among saved groups). */
	const nameOf = (p: Proposal) => names[p.id] ?? p.create?.name ?? p.name;
	const isLatestError = (i: number) => i === turns.length - 1 && !pending;
</script>

{#snippet avatar()}
	<span
		class="mt-0.5 flex size-7 flex-none items-center justify-center rounded-full border border-divider bg-card text-primary"
		aria-hidden="true"
	>
		<SparklesIcon class="size-3.5" />
	</span>
{/snippet}

<div class="relative flex min-h-0 flex-1 flex-col">
	<div class="min-h-0 flex-1 overflow-y-auto" bind:this={scroller}>
		{#if !usable}
			<EmptyState variant="onboarding" title="Group your runs">
				{#snippet lead()}
					A group is a set of runs that compare one change, such as a learning-rate sweep or an
					ablation. Pick its runs with filters and by hand, and Flock shows only what varies.
				{/snippet}
				<div
					class="flex items-start gap-3 rounded-xl border border-divider bg-card px-4 py-3.5 text-[13px] text-muted-foreground"
				>
					<SparklesIcon class="mt-0.5 size-4 flex-none text-faint" />
					<p>
						{refused
							? 'The assistant that proposes groups isn’t available to you here.'
							: 'The assistant that proposes groups is off for this organization.'}
						An org admin can turn it on in the organization’s settings.
					</p>
				</div>
				{#snippet actions()}
					<Button onclick={() => onmanual()}>Make a group by hand</Button>
				{/snippet}
			</EmptyState>
		{:else if !rows.length}
			<EmptyState variant="onboarding" title="No runs to group yet">
				{#snippet lead()}
					Groups compare runs that each change one thing. Log a few runs to this project, then come
					back and Flock proposes groups for them.
				{/snippet}
			</EmptyState>
		{:else}
			<EmptyState variant="onboarding" title="Group your runs" class="pb-0!">
				{#snippet lead()}
					Flock looks at your runs and proposes groups that each compare one change. Ask for
					changes, then create them.
				{/snippet}
			</EmptyState>

			<div class="mx-auto grid w-full max-w-[44rem] gap-7 px-5 pt-10 pb-56" aria-live="polite">
				{#each turns as turn, i (i)}
					{#if turn.kind === 'user'}
						<div
							class="ml-16 justify-self-end rounded-2xl rounded-br-md bg-secondary px-4 py-2.5 text-[15px] leading-[1.45] whitespace-pre-wrap"
						>
							{turn.text}
						</div>
					{:else if turn.kind === 'error'}
						<div class="flex gap-3">
							{@render avatar()}
							<div
								class="grid flex-1 gap-3 rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-3"
								role="alert"
							>
								<p class="text-[15px]">{turn.text}</p>
								{#if isLatestError(i)}
									<div class="flex flex-wrap items-center gap-2">
										<Button size="sm" onclick={() => propose()}>Try again</Button>
										<Button size="sm" variant="ghost" onclick={() => onmanual()}>
											Make a group by hand
										</Button>
									</div>
								{/if}
							</div>
						</div>
					{:else}
						{@const a = turn.answer}
						{@const live = isLatest(i)}
						<div class="flex gap-3">
							{@render avatar()}
							<div class="grid min-w-0 flex-1 gap-3">
								<p class="text-[15px] leading-[1.55] whitespace-pre-wrap">{a.message}</p>
								{#if live}
									{#each a.proposals as p, k (p.id)}
										{#if !removed[p.id]}
											<ProposalCard
												{entity}
												{project}
												proposal={p}
												color={turn.colors[k]}
												live={!creating}
												bind:included={() => included[p.id] ?? true, (v) => (included[p.id] = v)}
												bind:name={() => nameOf(p), (v) => (names[p.id] = v)}
												onremove={() => (removed[p.id] = true)}
												status={statuses[p.id] ?? null}
												failure={failures[p.id] ?? null}
											/>
										{/if}
									{/each}
									{#if a.proposals.length}
										<p class="text-[13px] text-muted-foreground">
											{#if ungrouped}
												{count(ungrouped, 'run')}
												{ungrouped === 1 ? 'stays' : 'stay'} ungrouped, still on Compare and the Runs
												page.
											{:else}
												Every run is in a group.
											{/if}
											{#if Object.values(removed).some(Boolean)}
												<button
													type="button"
													class="ml-1 text-foreground underline-offset-2 hover:underline"
													onclick={() => (removed = {})}
												>
													Bring back removed groups
												</button>
											{/if}
										</p>
									{/if}
								{:else if a.proposals.length}
									<!-- An earlier set, as a summary; it opens read only. -->
									<div class="rounded-xl border border-divider">
										<button
											type="button"
											class="flex w-full items-center gap-2 px-3.5 py-2.5 text-left text-[13px] outline-none hover:bg-muted/50 focus-visible:ring-2 focus-visible:ring-ring/50"
											aria-expanded={!!expanded[i]}
											onclick={() => (expanded[i] = !expanded[i])}
										>
											<ChevronRightIcon
												class={[
													'size-3.5 flex-none text-muted-foreground transition-transform',
													expanded[i] && 'rotate-90'
												]}
											/>
											<span class="text-muted-foreground">Earlier proposal</span>
											<span class="flex min-w-0 flex-1 flex-wrap gap-x-3 gap-y-1">
												{#each a.proposals as p, k (p.id)}
													<span class="flex min-w-0 items-center gap-1.5">
														<span
															class="size-2 flex-none rounded-[2px]"
															style:background={turn.colors[k]}
														></span>
														<span class="truncate">{p.name}</span>
														<span class="text-faint tabular-nums">{p.runs.length}</span>
													</span>
												{/each}
											</span>
										</button>
										{#if expanded[i]}
											<div class="grid gap-3 border-t border-divider p-3">
												{#each a.proposals as p, k (p.id)}
													<ProposalCard
														{entity}
														{project}
														proposal={p}
														color={turn.colors[k]}
														live={false}
													/>
												{/each}
											</div>
										{/if}
									</div>
								{/if}
								{#if live && !a.proposals.length}
									<div class="flex flex-wrap gap-2">
										<Button variant="outline" size="sm" onclick={() => onmanual()}>
											Make a group by hand
										</Button>
									</div>
								{/if}
							</div>
						</div>
						{#if live && a.proposals.length && !pending}
							<div
								class="sticky bottom-36 z-10 ml-10 flex flex-wrap items-center gap-2 rounded-xl border border-divider bg-card px-3 py-2.5 shadow-md"
							>
								<Button disabled={creating || !chosen.length} onclick={create}>
									{#if creating}
										Creating {Math.min(createdCount + 1, chosen.length)} of {chosen.length}…
									{:else if createdCount}
										Create the rest
									{:else}
										Create {count(chosen.length, 'group')}
									{/if}
								</Button>
								<Button
									variant="outline"
									disabled={creating}
									onclick={() => {
										const first = chosen[0] ?? shown[0];
										onmanual(first, first && nameOf(first));
									}}
								>
									Edit manually
								</Button>
								{#if createdCount && !creating}
									<span class="text-[13px] text-muted-foreground">
										{createdCount} created.
										<a class="text-foreground hover:underline" href={groupsHref(entity, project)}>
											Go to Compare
										</a>
									</span>
								{:else}
									<span class="ml-auto text-xs text-muted-foreground">
										Nothing is saved until you create them.
									</span>
								{/if}
							</div>
						{/if}
					{/if}
				{/each}

				{#if pending}
					<!-- Thinking: what it's doing, and the shape of what's coming. -->
					<div class="flex gap-3" role="status">
						{@render avatar()}
						<div class="grid min-w-0 flex-1 gap-3">
							<p class="flex items-baseline gap-2 text-[15px]">
								<span class="thinking">{thinkingText(seconds, rows.length, varying, !!latest)}</span
								>
								<span class="text-xs text-faint tabular-nums">{seconds}s</span>
							</p>
							{#each [0, 1] as k (k)}
								<div
									class="relative grid gap-2.5 overflow-hidden rounded-xl border border-divider py-3.5 pr-3.5 pl-5"
									style:opacity={k ? 0.6 : 1}
								>
									<span
										class="absolute inset-y-0 left-0 w-[3px] animate-pulse bg-muted-foreground/25"
									></span>
									<span class="h-4 w-48 animate-pulse rounded bg-muted"></span>
									<span class="h-3 w-80 max-w-full animate-pulse rounded bg-muted"></span>
									<span class="flex gap-1">
										{#each [0, 1, 2, 3, 4] as c (c)}
											<span
												class="h-9 w-12 animate-pulse rounded-md bg-muted"
												style:animation-delay="{c * 120}ms"
											></span>
										{/each}
									</span>
								</div>
							{/each}
						</div>
					</div>
				{/if}
			</div>
		{/if}
	</div>

	{#if usable && rows.length}
		<!-- The composer, pinned under the conversation as a chat's. -->
		<div
			class="pointer-events-none absolute inset-x-0 bottom-0 bg-linear-to-t from-background from-60% to-transparent pt-10"
		>
			<div class="pointer-events-auto mx-auto w-full max-w-[44rem] px-5 pb-5">
				<form
					class="flex items-end gap-2 rounded-2xl border border-input bg-card py-2 pr-2 pl-4 shadow-sm focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/50"
					onsubmit={send}
				>
					<label class="min-w-0 flex-1">
						<span class="sr-only">Ask for changes</span>
						<textarea
							bind:this={input}
							bind:value={text}
							{onkeydown}
							rows="1"
							class="[field-sizing:content] max-h-40 min-h-7 w-full resize-none bg-transparent py-1 text-[15px] leading-6 outline-none placeholder:text-faint"
							placeholder={latest
								? 'Ask for changes, e.g. “merge the two LR sweeps”'
								: 'Describe how to group your runs'}></textarea>
					</label>
					{#if pending}
						<Button
							type="button"
							size="icon"
							variant="secondary"
							class="rounded-full"
							aria-label="Stop"
							title="Stop"
							onclick={() => controller?.abort()}
						>
							<SquareIcon class="size-3 fill-current" />
						</Button>
					{:else}
						<Button
							type="submit"
							size="icon"
							class="rounded-full"
							aria-label="Send"
							disabled={!text.trim() || creating}
						>
							<ArrowUpIcon />
						</Button>
					{/if}
				</form>
				{#if !asked}
					<div class="mt-2.5 flex flex-wrap items-center justify-center gap-1.5">
						<span class="mr-1 text-xs text-faint">Try</span>
						{#each prompts as s (s)}
							<button
								type="button"
								class="rounded-full border border-divider bg-card px-3 py-1 text-[13px] text-muted-foreground outline-none hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50 disabled:opacity-50"
								disabled={pending || creating}
								onclick={() => void propose(s)}
							>
								{s}
							</button>
						{/each}
					</div>
				{:else}
					<p class="mt-2 text-center text-xs text-faint">
						Enter to send · Shift+Enter for a new line
					</p>
				{/if}
			</div>
		</div>
	{/if}
</div>

<style>
	/* The thinking line shimmers, as a chat's does. */
	.thinking {
		background: linear-gradient(
				90deg,
				var(--muted-foreground) 0%,
				var(--foreground) 50%,
				var(--muted-foreground) 100%
			)
			0 0 / 200% 100%;
		background-clip: text;
		color: transparent;
		animation: shimmer 2.2s linear infinite;
	}
	@keyframes shimmer {
		to {
			background-position: -200% 0;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.thinking {
			animation: none;
		}
	}
</style>
