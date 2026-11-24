<script lang="ts">
	import CalendarIcon from '@lucide/svelte/icons/calendar';
	import EyeOffIcon from '@lucide/svelte/icons/eye-off';
	import XIcon from '@lucide/svelte/icons/x';
	import { enhance } from '$app/forms';
	import MetaItem from '#lib/components/MetaItem.svelte';
	import MetaRow from '#lib/components/MetaRow.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import { dismissedBy, warningText } from '#lib/budget.ts';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import type { BudgetWarnings } from '#lib/types.ts';
	import { dateTime, monthName } from '#lib/utils.ts';
	import { orgHref } from '#lib/workspace/url.ts';

	/** Admins only: the month's budget warnings, and those dismissed for everyone. */
	let { org, warnings }: { org: string; warnings: BudgetWarnings } = $props();

	const dismissing = new Mutation({ toastErrors: true, success: () => 'Warning dismissed' });
	const restoring = new Mutation({ toastErrors: true, success: () => 'Warning shown again' });
</script>

<p class="mt-2 text-muted-foreground">
	GPU hours Flock can’t price, from GPU types without a price or runs with unknown GPUs.
</p>

<h3 class="mt-6 text-[13px] font-medium text-muted-foreground">
	{monthName(warnings.month)}
</h3>
{#if warnings.warnings.length}
	<ul class="mt-2 border-t border-divider">
		{#each warnings.warnings as w (w.id)}
			<li class="flex min-h-12 items-center gap-3 border-b border-divider py-2">
				<p class="min-w-0 flex-1">
					{warningText(w)}
					{#if w.kind === 'unpriced'}<a
							class="text-link hover:underline"
							href={orgHref(org, 'budget')}>Set prices</a
						>{/if}
				</p>
				<form method="POST" action="?/dismissWarning" use:enhance={dismissing.enhance}>
					<input type="hidden" name="warning" value={w.id} />
					<input type="hidden" name="fingerprint" value={w.fingerprint} />
					<Button
						type="submit"
						variant="ghost"
						size="icon-sm"
						class="text-muted-foreground"
						aria-label="Dismiss for everyone"
						title="Dismiss for everyone"
						disabled={dismissing.pending}
					>
						<XIcon />
					</Button>
				</form>
			</li>
		{/each}
	</ul>
{:else}
	<p class="mt-2 border-y border-divider py-3 text-[13px] text-muted-foreground">
		Nothing to warn about.
	</p>
{/if}

{#if warnings.dismissed.length}
	<h3 class="mt-6 text-[13px] font-medium text-muted-foreground">Dismissed</h3>
	<ul class="mt-2 border-t border-divider">
		{#each warnings.dismissed as w (w.id)}
			<li class="flex min-h-14 items-center gap-3 border-b border-divider py-2">
				<div class="min-w-0 flex-1">
					<p>{warningText(w)}</p>
					<MetaRow class="mt-0.5">
						<MetaItem icon={CalendarIcon}>{monthName(w.month)}</MetaItem>
						<MetaItem icon={EyeOffIcon}>{dismissedBy(w, dateTime)}</MetaItem>
					</MetaRow>
				</div>
				<form method="POST" action="?/restoreWarning" use:enhance={restoring.enhance}>
					<input type="hidden" name="warning" value={w.id} />
					<Button type="submit" variant="outline" size="sm" disabled={restoring.pending}>
						Show again
					</Button>
				</form>
			</li>
		{/each}
	</ul>
{/if}
