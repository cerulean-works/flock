<script lang="ts" module>
	import type { AssistAction, Reading } from '#lib/groups.ts';

	/** One line of the assistant's thread (D191). */
	export type Entry =
		| { kind: 'request'; text: string }
		| {
				kind: 'action';
				action: AssistAction;
				/** What it did, e.g. "21 runs match". */
				result: string;
				undone: boolean;
		  }
		/** A tick by hand, logged in the same thread. */
		| { kind: 'manual'; text: string }
		| { kind: 'error'; text: string };
</script>

<script lang="ts">
	import ArrowUpIcon from '@lucide/svelte/icons/arrow-up';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import MinusIcon from '@lucide/svelte/icons/minus';
	import SparklesIcon from '@lucide/svelte/icons/sparkles';
	import { tick } from 'svelte';
	import { page } from '$app/state';
	import ResizeHandle from '#lib/components/ResizeHandle.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';
	import { bestLabel, displayKey } from '#lib/groups.ts';
	import { ASSISTANT_W, saveWidth } from '#lib/widths.ts';
	import { opLabel, UNARY, type RunCondition } from '#lib/workspace/filters.ts';

	/**
	 * The assistant on the create and edit page (D191). A request comes back as one action
	 * card: **Set filters** with chips, **Added by hand** or **Dropped** with run names, **Set
	 * best by**, or **Nothing changed**, each with **Undo**. A chip picked with less than 0.8
	 * shows its probability, and lists the other readings. The model never asks anything back,
	 * so there's nothing to answer here.
	 */

	interface Props {
		entries: Entry[];
		pending: boolean;
		onsend: (text: string) => void;
		/** Undo the action at `index` of `entries`. */
		onundo: (index: number) => void;
		/** Use another reading of the condition at `condition` of the action at `index`. */
		onpick: (index: number, condition: number, reading: Reading) => void;
		/** What the empty thread offers to ask; a click sends it. */
		suggestions: string[];
	}

	let { entries, pending, onsend, onundo, onpick, suggestions }: Props = $props();

	/** Dragged from its left edge, and kept in a cookie like the sidebar's width. */
	let width = $state<number>(page.data.shell?.assistantW ?? ASSISTANT_W.initial);

	let text = $state('');
	let thread = $state<HTMLElement>();

	/** Below this, a chip shows how likely its reading was (D191). */
	const SURE = 0.8;

	function send(e: SubmitEvent) {
		e.preventDefault();
		const t = text.trim();
		if (!t || pending) return;
		text = '';
		onsend(t);
	}

	// The thread follows its newest line.
	$effect(() => {
		void entries.length;
		void tick().then(() => thread?.scrollTo({ top: thread.scrollHeight }));
	});

	const conditionText = (c: RunCondition) =>
		`${displayKey(c.key)} ${opLabel(c.op)}${UNARY.has(c.op) ? '' : ` ${String(c.value)}`}`;
	const pct = (p: number) => `${Math.round(p * 100)}%`;

	const TITLES: Record<AssistAction['type'], string> = {
		filter: 'Set filters',
		add_runs: 'Added by hand',
		drop_runs: 'Dropped',
		set_best: 'Set best by',
		none: 'Nothing changed'
	};
	const DOTS: Record<AssistAction['type'], string> = {
		filter: 'bg-state-running',
		add_runs: 'bg-state-finished',
		drop_runs: 'bg-faint',
		set_best: 'bg-state-running',
		none: 'bg-faint'
	};
	const CHIPS: Record<AssistAction['type'], string> = {
		filter: 'bg-state-running/12 text-deep',
		add_runs: 'bg-state-finished/12 text-state-finished',
		drop_runs: 'bg-muted text-muted-foreground line-through',
		set_best: 'bg-state-running/12 text-deep',
		none: ''
	};
</script>

<aside
	class="relative flex flex-none flex-col border-l border-divider bg-card/50 max-lg:hidden"
	style:width="{width}px"
	aria-label="Assistant"
>
	<ResizeHandle
		edge="left"
		value={width}
		bounds={ASSISTANT_W}
		label="Resize the assistant"
		onresize={(px) => (width = px)}
		oncommit={(px) => saveWidth(ASSISTANT_W, px)}
	/>
	<div class="flex h-10 flex-none items-center gap-2 border-b border-divider px-4">
		<SparklesIcon class="size-4 text-primary" />
		<span class="font-medium">Assistant</span>
		<span class="text-xs text-muted-foreground">sets filters and picks runs</span>
	</div>
	<div
		class="grid min-h-0 flex-1 content-start gap-2.5 overflow-y-auto p-3 text-[13px]"
		bind:this={thread}
	>
		{#each entries as entry, i (i)}
			{#if entry.kind === 'request'}
				<div class="ml-8 justify-self-end rounded-lg bg-secondary px-3 py-2">{entry.text}</div>
			{:else if entry.kind === 'manual'}
				<div class="flex items-center gap-1.5 px-1 text-xs text-muted-foreground">
					<MinusIcon class="size-3" />{entry.text}
				</div>
			{:else if entry.kind === 'error'}
				<div class="px-1 text-xs text-destructive">{entry.text}</div>
			{:else}
				{@const a = entry.action}
				<div
					class={[
						'grid gap-2 rounded-lg border border-divider bg-background p-2.5',
						entry.undone && 'opacity-60'
					]}
				>
					<div class="flex items-center gap-2 font-medium">
						<span class={['size-1.5 rounded-full', DOTS[a.type]]}></span>
						{TITLES[a.type]}
						{#if entry.undone}
							<span class="text-xs font-normal text-muted-foreground">undone</span>
						{:else if a.type !== 'none'}
							<Button
								variant="ghost"
								size="xs"
								class="ml-auto text-muted-foreground"
								onclick={() => onundo(i)}
							>
								Undo
							</Button>
						{/if}
					</div>
					{#if a.type === 'filter' && a.conditions.length}
						<div class="flex flex-wrap gap-1.5">
							{#each a.conditions as c, k (k)}
								{#if c.alternatives.length && !entry.undone}
									<DropdownMenu.Root>
										<DropdownMenu.Trigger
											class="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 font-mono text-[12px] hover:brightness-95 {CHIPS.filter}"
											title="Other readings of the request"
										>
											{conditionText(c)}
											{#if c.probability < SURE}
												<span class="font-sans text-[11px] text-muted-foreground"
													>{pct(c.probability)}</span
												>
											{/if}
											<ChevronDownIcon class="size-3 text-muted-foreground" />
										</DropdownMenu.Trigger>
										<DropdownMenu.Content align="start" class="w-64">
											<DropdownMenu.Label>Other readings</DropdownMenu.Label>
											{#each c.alternatives as alt, j (j)}
												<DropdownMenu.Item onSelect={() => onpick(i, k, alt)}>
													<span class="min-w-0 flex-1 truncate font-mono text-[12px]">
														{conditionText(alt)}
													</span>
													<span class="text-xs text-muted-foreground">{pct(alt.probability)}</span>
												</DropdownMenu.Item>
											{/each}
										</DropdownMenu.Content>
									</DropdownMenu.Root>
								{:else}
									<span class={['rounded-md px-1.5 py-0.5 font-mono text-[12px]', CHIPS.filter]}>
										{conditionText(c)}
										{#if c.probability < SURE}
											<span class="font-sans text-[11px] text-muted-foreground"
												>{pct(c.probability)}</span
											>
										{/if}
									</span>
								{/if}
							{/each}
						</div>
					{:else if (a.type === 'add_runs' || a.type === 'drop_runs') && a.runs.length}
						<div class="flex flex-wrap gap-1.5">
							{#each a.runs as r (r.id)}
								<span class={['rounded-md px-1.5 py-0.5 text-[12px]', CHIPS[a.type]]} title={r.id}>
									{r.name}
									{#if r.probability < SURE}
										<span class="text-[11px] text-muted-foreground no-underline"
											>{pct(r.probability)}</span
										>
									{/if}
								</span>
							{/each}
						</div>
					{:else if a.type === 'set_best' && a.best}
						<div>
							<span class={['rounded-md px-1.5 py-0.5 text-[12px]', CHIPS.set_best]}>
								{bestLabel(a.best.key, a.best.order)}
							</span>
						</div>
					{/if}
					{#if entry.result}
						<span class="text-xs text-muted-foreground">{entry.result}</span>
					{/if}
				</div>
			{/if}
		{:else}
			<!-- The empty thread, as a chat's: a few requests to start from, each sent on a click. -->
			<div class="grid gap-2 pt-6">
				<p class="px-1 text-xs text-muted-foreground">Try asking</p>
				{#each suggestions as s (s)}
					<button
						type="button"
						class="rounded-lg border border-divider bg-background px-3 py-2 text-left outline-none hover:bg-muted/60 focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50"
						disabled={pending}
						onclick={() => onsend(s)}
					>
						{s}
					</button>
				{/each}
			</div>
		{/each}
		{#if pending}
			<div class="flex items-center gap-1.5 px-1 text-xs text-muted-foreground">
				<LoaderCircleIcon class="size-3.5 animate-spin" /> Working…
			</div>
		{/if}
	</div>
	<form class="flex-none border-t border-divider p-3" onsubmit={send}>
		<label
			class="flex items-center gap-2 rounded-lg border border-input bg-background py-1 pr-1 pl-3 focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/50"
		>
			<span class="sr-only">Ask the assistant</span>
			<input
				bind:value={text}
				class="min-w-0 flex-1 bg-transparent text-[13px] outline-none placeholder:text-faint"
				placeholder="e.g. “only batch sizes up to 128”"
				autocomplete="off"
			/>
			<Button type="submit" size="icon-sm" aria-label="Send" disabled={!text.trim() || pending}>
				<ArrowUpIcon />
			</Button>
		</label>
	</form>
</aside>
