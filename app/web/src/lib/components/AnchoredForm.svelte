<script lang="ts" module>
	/**
	 * The ⋯ menu open now, called from one of its items: a form the item opens anchors to it.
	 * The menu is still open while its item's onSelect runs.
	 */
	export function openMenuTrigger(): HTMLElement | null {
		return document.querySelector<HTMLElement>('[aria-haspopup="menu"][data-state="open"]');
	}
</script>

<script lang="ts">
	import type { Snippet } from 'svelte';
	import { fromAction } from 'svelte/attachments';
	import { MediaQuery } from 'svelte/reactivity';
	import { enhance as enhanceForm, type SubmitFunction } from '$app/forms';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Popover from '#lib/components/ui/popover/index.js';
	import * as Sheet from '#lib/components/ui/sheet/index.js';

	// A one-field form (a name, a tag, a path) opened from a control: a small panel under it, a
	// bottom sheet on phones. Enter confirms, Esc or a click elsewhere closes. While it's sent it
	// stays open: the request decides how it ends.
	interface Props {
		open: boolean;
		/** The control it opens from. A trigger it renders itself needs none. */
		anchor?: HTMLElement | null;
		/** Renders the control that opens it, given the props to spread on it. */
		trigger?: Snippet<[Record<string, unknown>]>;
		title: string;
		/** What the button says, as "Create folder" or "Rename". */
		submit: string;
		pending?: boolean;
		error?: string | null;
		disabled?: boolean;
		/** Sent from the page, or (with `action` and `enhance`) to a form action. */
		onsubmit?: () => void | Promise<void>;
		action?: string;
		enhance?: SubmitFunction;
		/** Once it has closed, to clear the field. */
		onclosed?: () => void;
		/** The field, and any status under it. */
		children: Snippet;
		/** A note under the field, which an error takes the place of. */
		note?: Snippet;
		align?: 'start' | 'end';
		/** Room for a path. */
		wide?: boolean;
	}

	let {
		open = $bindable(),
		anchor = null,
		trigger,
		title,
		submit,
		pending = false,
		error = null,
		disabled = false,
		onsubmit,
		action,
		enhance,
		onclosed,
		children,
		note,
		align = 'end',
		wide = false
	}: Props = $props();

	const phone = new MediaQuery('(max-width: 639px)');
	let panel = $state<HTMLElement | null>(null);
	const titleId = $props.id();

	function setOpen(o: boolean) {
		open = pending ? true : o;
	}

	/** Into the field, its text selected, so a rename types over the old name. */
	function focusField(e: Event) {
		e.preventDefault();
		const field = panel?.querySelector<HTMLInputElement>('input:not([type=hidden]), textarea');
		field?.focus();
		field?.select();
	}

	/** Back to the control it came from, when that's still on the page. */
	function focusBack(e: Event) {
		if (!anchor) return;
		e.preventDefault();
		if (anchor.isConnected) anchor.focus({ preventScroll: true });
	}

	function send(e: SubmitEvent) {
		if (enhance) return;
		e.preventDefault();
		void onsubmit?.();
	}

	// The ⋯ it came from opens its menu again: the form gives way to it.
	$effect(() => {
		if (!open || !anchor || anchor.getAttribute('aria-haspopup') !== 'menu') return;
		const el = anchor;
		const close = () => setOpen(false);
		el.addEventListener('pointerdown', close);
		return () => el.removeEventListener('pointerdown', close);
	});

	/**
	 * A click on the button it opens from toggles it, rather than closing and reopening it. A ⋯
	 * menu it came from just closes it, as any click elsewhere does.
	 */
	function outside(e: PointerEvent) {
		if (anchor?.contains(e.target as Node) && anchor.getAttribute('aria-haspopup') !== 'menu')
			e.preventDefault();
	}
</script>

{#snippet form()}
	<form
		class="grid gap-2.5"
		method={enhance ? 'POST' : undefined}
		{action}
		onsubmit={send}
		{@attach enhance && fromAction(enhanceForm, () => enhance as never)}
	>
		<h2 class="truncate text-[13px] font-medium text-foreground" id={titleId}>
			{title}
		</h2>
		{@render children()}
		{#if error}
			<p class="text-[12px] text-destructive" role="alert">
				{error.charAt(0).toUpperCase() + error.slice(1)}
			</p>
		{:else if note}
			<div class="text-[12px] break-words text-muted-foreground">{@render note()}</div>
		{/if}
		<div class="flex justify-end gap-1.5">
			<Button
				type="button"
				variant="ghost"
				size="sm"
				class="text-muted-foreground"
				disabled={pending}
				onclick={() => setOpen(false)}>Cancel</Button
			>
			<Button type="submit" size="sm" disabled={disabled || pending} {pending}>{submit}</Button>
		</div>
	</form>
{/snippet}

{#if phone.current}
	{#if trigger}
		{@render trigger({ onclick: () => setOpen(true), 'aria-haspopup': 'dialog' })}
	{/if}
	<Sheet.Root bind:open={() => open, setOpen} onOpenChangeComplete={(o) => o || onclosed?.()}>
		<Sheet.Content
			bind:ref={panel}
			side="bottom"
			showCloseButton={false}
			class="rounded-t-xl p-4 pb-[max(1rem,env(safe-area-inset-bottom))]"
			role="dialog"
			aria-labelledby={titleId}
			onOpenAutoFocus={focusField}
			onCloseAutoFocus={focusBack}
			escapeKeydownBehavior={pending ? 'ignore' : 'close'}
			interactOutsideBehavior={pending ? 'ignore' : 'close'}
		>
			{@render form()}
		</Sheet.Content>
	</Sheet.Root>
{:else}
	<Popover.Root bind:open={() => open, setOpen} onOpenChangeComplete={(o) => o || onclosed?.()}>
		{#if trigger}
			<Popover.Trigger>
				{#snippet child({ props })}
					{@render trigger(props)}
				{/snippet}
			</Popover.Trigger>
		{/if}
		<Popover.Content
			bind:ref={panel}
			customAnchor={trigger ? undefined : anchor}
			onInteractOutside={outside}
			{align}
			sideOffset={6}
			collisionPadding={16}
			class={[wide ? 'w-96' : 'w-80', 'p-3']}
			role="dialog"
			aria-labelledby={titleId}
			onOpenAutoFocus={focusField}
			onCloseAutoFocus={focusBack}
			escapeKeydownBehavior={pending ? 'ignore' : 'close'}
			interactOutsideBehavior={pending ? 'ignore' : 'close'}
		>
			{@render form()}
		</Popover.Content>
	</Popover.Root>
{/if}
