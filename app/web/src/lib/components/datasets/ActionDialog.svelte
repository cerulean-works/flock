<script lang="ts" module>
	/** One text field the dialog asks for. */
	export interface Field {
		name: string;
		label: string;
		/** Shown after the label, muted: "optional". */
		hint?: string;
		type?: 'text' | 'number';
		value?: string;
		placeholder?: string;
		required?: boolean;
		min?: number;
		step?: number | 'any';
	}
</script>

<script lang="ts">
	import { enhance } from '$app/forms';
	import * as AlertDialog from '#lib/components/ui/alert-dialog/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import FormError from '#lib/components/FormError.svelte';
	import { Mutation } from '#lib/data/mutation.svelte.ts';

	interface Props {
		open: boolean;
		title: string;
		description: string;
		/** The page action to post to, with `fields` hidden in the form. */
		action: string;
		fields?: Record<string, string | number>;
		/** A field to fill in, if any. */
		input?: Field | null;
		confirm: string;
		pending: string;
		/** Toast once it's done, from the action's result. */
		success: (data: Record<string, unknown>) => string;
		/** A destructive change gets the destructive button. */
		destructive?: boolean;
	}

	let {
		open = $bindable(),
		title,
		description,
		action,
		fields = {},
		input = null,
		confirm,
		pending,
		success,
		destructive = false
	}: Props = $props();

	const mutation = new Mutation({ success: (d) => success(d), done: () => (open = false) });
	const id = $props.id();
</script>

<!-- A change to a dataset, confirmed, with at most one field: a tag's name, a message, days. -->
<AlertDialog.Root bind:open onOpenChange={(o) => o || mutation.reset()}>
	<AlertDialog.Content class="sm:max-w-md">
		<AlertDialog.Header>
			<AlertDialog.Title>{title}</AlertDialog.Title>
			<AlertDialog.Description>{description}</AlertDialog.Description>
		</AlertDialog.Header>
		<form method="POST" {action} class="grid gap-4" use:enhance={mutation.enhance}>
			{#each Object.entries(fields) as [name, value] (name)}
				<input type="hidden" {name} {value} />
			{/each}
			{#if input}
				<div class="grid gap-2">
					<Label for="{id}-input">
						{input.label}
						{#if input.hint}<span class="font-normal text-muted-foreground">{input.hint}</span>{/if}
					</Label>
					<Input
						id="{id}-input"
						name={input.name}
						type={input.type ?? 'text'}
						value={input.value ?? ''}
						placeholder={input.placeholder}
						required={input.required}
						min={input.min}
						step={input.step}
						autocomplete="off"
						spellcheck="false"
						aria-invalid={!!mutation.error}
					/>
				</div>
			{/if}
			<FormError error={mutation.error} />
			<AlertDialog.Footer>
				<AlertDialog.Cancel>Cancel</AlertDialog.Cancel>
				<Button
					type="submit"
					variant={destructive ? 'destructive' : 'default'}
					disabled={mutation.pending}
				>
					{mutation.pending ? pending : confirm}
				</Button>
			</AlertDialog.Footer>
		</form>
	</AlertDialog.Content>
</AlertDialog.Root>
