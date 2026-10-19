<script lang="ts">
	import RotateCcwIcon from '@lucide/svelte/icons/rotate-ccw';
	import XIcon from '@lucide/svelte/icons/x';
	import { untrack } from 'svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import * as Select from '#lib/components/ui/select/index.js';
	import { Slider } from '#lib/components/ui/slider/index.js';
	import { Switch } from '#lib/components/ui/switch/index.js';
	import * as Tabs from '#lib/components/ui/tabs/index.js';
	import { SYSTEM_X_AXES, axisChoiceLabel, resolveAxis, type AxisKind } from '#lib/chart/axes.ts';
	import { LINE_WIDTH_MAX, LINE_WIDTH_MIN, SMOOTHING_MAX } from '#lib/workspace/layout.ts';
	import {
		configOf,
		draftOf,
		resolvePanel,
		type PanelDraft,
		type Tristate
	} from '#lib/workspace/panels.ts';
	import { getWorkspace, metricColor } from '#lib/workspace/workspace.svelte.ts';
	import Autocomplete, { type AutocompleteOption } from './Autocomplete.svelte';
	import LinePlot from './LinePlot.svelte';
	import PanelLegend from './PanelLegend.svelte';
	import XAxisPicker from './XAxisPicker.svelte';

	/**
	 * W&B's "Edit panel": the panel's settings beside a live preview of it. Edits show in the
	 * preview only; Apply keeps them for this project in this browser, Cancel drops them.
	 */

	const ws = getWorkspace();

	const panel = $derived(ws.editing == null ? -1 : ws.findPanel(ws.editing));
	const def = $derived(panel === -1 ? null : ws.meta.panels[panel]);
	/** The panel's own metric keys, which a draft plotting just those leaves unset. */
	const own = $derived(def ? def.metrics.map((m) => ws.meta.metrics[m].key) : []);

	// Closes here first; `ws.editing` is cleared once the closing animation is over, so the
	// dialog keeps its content while it fades.
	let open = $derived(panel !== -1);

	// The form, filled when the editor opens. Untracked, so live updates and other tabs don't
	// overwrite what is being typed; replaced (never mutated) as it's edited.
	let draft = $derived.by((): PanelDraft | null => {
		const p = panel;
		return p === -1
			? null
			: untrack(() => draftOf(ws.panelConfig(p), own, ws.axisOf(ws.panels[p]).id));
	});

	function set<K extends keyof PanelDraft>(key: K, value: PanelDraft[K]) {
		if (draft) draft = { ...draft, [key]: value };
	}

	/** The x axis the draft plots on; its x bounds are in its units. */
	const axis = $derived.by(() => {
		if (!def || !draft) return null;
		const first = def.stream === 'history' ? draft.metrics[0] : undefined;
		const step = first ? ws.stepMetric(first) : null;
		return resolveAxis(draft.xAxis ?? ws.xAxis, def.stream, step);
	});
	const UNITS: Record<AxisKind, string> = {
		step: 'steps',
		number: '',
		duration: 'seconds',
		time: 'seconds since 1970'
	};

	/** What following the workspace means for this panel. */
	const workspaceAxis = $derived(
		def?.stream === 'system' && !SYSTEM_X_AXES.includes(ws.xAxis)
			? axisChoiceLabel('_runtime')
			: axisChoiceLabel(ws.xAxis)
	);

	/** Another x axis, whose units the x bounds typed so far aren't in. */
	function setAxis(v: string | null) {
		if (draft) draft = { ...draft, xAxis: v, xMin: '', xMax: '' };
	}

	const result = $derived(
		draft && def && axis ? configOf(draft, { title: def.title, metrics: own }, axis.id) : null
	);
	const errors = $derived(result?.errors ?? {});
	const valid = $derived(!!result && !Object.keys(result.errors).length);
	/** The panel as the draft would draw it. */
	const preview = $derived(
		def && result
			? resolvePanel(def, result.config, ws.meta.metrics, (s, k) => ws.metricOf(s, k))
			: null
	);
	/** The title it gets without one set: its own, or its metrics' names. */
	const autoTitle = $derived(
		def && result
			? resolvePanel(def, { ...result.config, title: undefined }, ws.meta.metrics, (s, k) =>
					ws.metricOf(s, k)
				).title
			: ''
	);
	const zoomed = $derived(panel !== -1 && !!axis && ws.zoomOf(panel, axis.id) != null);

	/** Metrics the panel can plot: those of its stream, so they share its x axis. */
	const options = $derived.by((): AutocompleteOption[] => {
		if (!def || !draft) return [];
		const picked = draft.metrics;
		return ws.meta.metrics
			.filter((m) => m.stream === def.stream && !picked.includes(m.key))
			.map((m) => ({ value: m.key, hint: m.label !== m.key ? m.label : undefined }));
	});
	let adding = $state('');

	function addMetric(key: string) {
		if (!draft || draft.metrics.includes(key) || ws.metricOf(def!.stream, key) === undefined)
			return;
		set('metrics', [...draft.metrics, key]);
		adding = '';
	}

	const labelOf = (key: string) => {
		const m = def ? ws.metricOf(def.stream, key) : undefined;
		return m === undefined ? key : ws.meta.metrics[m].label;
	};

	function apply() {
		if (!valid || !result) return;
		ws.setPanelConfig(panel, result.config);
		open = false;
	}

	const onOff = (on: boolean) => (on ? 'on' : 'off');
	const TRI: Record<Exclude<Tristate, 'inherit'>, string> = { on: 'On', off: 'Off' };
</script>

{#snippet tristate(
	id: string,
	label: string,
	value: Tristate,
	inherit: string,
	onchange: (v: Tristate) => void
)}
	<div class="flex items-center justify-between gap-4">
		<Label for={id}>{label}</Label>
		<Select.Root type="single" {value} onValueChange={(v) => onchange(v as Tristate)}>
			<Select.Trigger {id} size="sm" class="w-40">
				{value === 'inherit' ? inherit : TRI[value]}
			</Select.Trigger>
			<Select.Content>
				<Select.Item value="inherit">{inherit}</Select.Item>
				<Select.Item value="on">On</Select.Item>
				<Select.Item value="off">Off</Select.Item>
			</Select.Content>
		</Select.Root>
	</div>
{/snippet}

{#snippet range(
	axis: 'x' | 'y',
	min: string,
	max: string,
	unit: string,
	onmin: (v: string) => void,
	onmax: (v: string) => void
)}
	{@const minError = errors[`${axis}Min`]}
	{@const maxError = errors[`${axis}Max`]}
	<div class="grid gap-2">
		<Label for="panel-{axis}-min">Range{unit ? ` (${unit})` : ''}</Label>
		<div class="grid grid-cols-2 gap-2">
			<Input
				id="panel-{axis}-min"
				value={min}
				placeholder="Min: auto"
				inputmode="decimal"
				autocomplete="off"
				aria-label="{axis.toUpperCase()} minimum"
				aria-invalid={!!minError}
				oninput={(e) => onmin(e.currentTarget.value)}
			/>
			<Input
				value={max}
				placeholder="Max: auto"
				inputmode="decimal"
				autocomplete="off"
				aria-label="{axis.toUpperCase()} maximum"
				aria-invalid={!!maxError}
				oninput={(e) => onmax(e.currentTarget.value)}
			/>
		</div>
		{#if minError || maxError}
			<p class="text-xs text-destructive">{minError ?? maxError}</p>
		{/if}
	</div>
{/snippet}

<Dialog.Root
	bind:open
	onOpenChangeComplete={(o) => {
		if (!o) ws.editing = null;
	}}
>
	<Dialog.Content
		class="flex h-[min(760px,calc(100dvh-2rem))] flex-col gap-0 p-0 sm:max-w-[min(1180px,calc(100%-2rem))]"
	>
		{#if def && draft && preview && axis}
			<Dialog.Header class="border-b border-divider px-5 pt-4 pb-3.5">
				<Dialog.Title>Edit panel</Dialog.Title>
				<Dialog.Description class="truncate">{def.section} · {preview.title}</Dialog.Description>
			</Dialog.Header>
			<div class="flex min-h-0 flex-1 max-md:flex-col">
				<!-- The preview: the panel as it would be, with the workspace's runs. -->
				<section
					class="flex min-h-72 min-w-0 flex-1 flex-col gap-3 px-5 pt-4 pb-3"
					aria-label="Preview"
				>
					<div class="flex min-h-8 items-center gap-2">
						<h3 class="min-w-0 flex-1 truncate font-medium" title={preview.title}>
							<span class="font-normal text-muted-foreground"
								>{preview.title.slice(0, preview.prefix)}</span
							>{preview.title.slice(preview.prefix)}
						</h3>
						{#if zoomed}
							<Button variant="link" size="xs" onclick={() => ws.setZoom(panel, null)}>
								Reset zoom
							</Button>
						{/if}
					</div>
					<PanelLegend view={preview} />
					<div class="min-h-0 flex-1">
						<LinePlot {panel} view={preview} surface="editor" active={open} />
					</div>
				</section>
				<aside
					class="flex w-[380px] flex-none flex-col border-l border-divider max-md:w-full max-md:border-t max-md:border-l-0"
					aria-label="Settings"
				>
					<Tabs.Root value="data" class="flex min-h-0 flex-1 flex-col gap-0">
						<Tabs.List variant="line" class="mx-5 mt-2 w-auto justify-start">
							<Tabs.Trigger value="data" class="flex-none">Data</Tabs.Trigger>
							<Tabs.Trigger value="display" class="flex-none">Display preferences</Tabs.Trigger>
						</Tabs.List>
						<Tabs.Content value="data" class="min-h-0 flex-1 overflow-y-auto px-5 pt-4 pb-6">
							<div class="grid gap-5">
								<div class="grid gap-2">
									<Label for="panel-title">Chart title</Label>
									<Input
										id="panel-title"
										value={draft.title}
										placeholder={autoTitle}
										autocomplete="off"
										oninput={(e) => set('title', e.currentTarget.value)}
									/>
								</div>

								<fieldset class="grid gap-3">
									<legend class="mb-3 text-xs text-muted-foreground">Y axis</legend>
									<div class="grid gap-2">
										<Label for="panel-add-metric">Metrics</Label>
										<ul class="flex flex-wrap gap-1.5" aria-label="Metrics plotted">
											{#each draft.metrics as key, k (key)}
												<li
													class="flex h-7 max-w-full min-w-0 items-center gap-1.5 rounded-md bg-muted pr-0.5 pl-2 text-[13px]"
													title={key}
												>
													<span
														class="h-0.5 w-3 flex-none rounded-full"
														style:background={ws.byMetric(preview)
															? metricColor(k)
															: 'var(--muted-foreground)'}
													></span>
													<span class="truncate">{labelOf(key)}</span>
													<Button
														variant="ghost"
														size="icon-xs"
														aria-label="Remove {labelOf(key)}"
														onclick={() =>
															set(
																'metrics',
																draft!.metrics.filter((m) => m !== key)
															)}
													>
														<XIcon />
													</Button>
												</li>
											{/each}
										</ul>
										<Autocomplete
											bind:value={adding}
											{options}
											mono
											placeholder="Add a metric…"
											aria-label="Add a metric"
											onPick={(o) => addMetric(o.value)}
										/>
										{#if errors.metrics}
											<p class="text-xs text-destructive">{errors.metrics}</p>
										{:else}
											<p class="text-xs text-muted-foreground">
												Plot several metrics on one chart. Only {def.stream === 'system'
													? 'system metrics'
													: 'logged metrics'} share this x axis.
											</p>
										{/if}
									</div>
									{@render range(
										'y',
										draft.yMin,
										draft.yMax,
										'',
										(v) => set('yMin', v),
										(v) => set('yMax', v)
									)}
									<div class="grid gap-2">
										<Label for="panel-y-label">Label</Label>
										<Input
											id="panel-y-label"
											value={draft.yLabel}
											placeholder="None"
											autocomplete="off"
											oninput={(e) => set('yLabel', e.currentTarget.value)}
										/>
									</div>
								</fieldset>

								<fieldset class="grid gap-3">
									<legend class="mb-3 text-xs text-muted-foreground">
										X axis · {axis.label}
									</legend>
									<div class="grid gap-2">
										<Label>Plotted against</Label>
										<XAxisPicker
											value={draft.xAxis}
											stream={def.stream}
											inherit="Workspace ({workspaceAxis})"
											onchange={setAxis}
										/>
										{#if def.stream === 'system'}
											<p class="text-xs text-muted-foreground">
												System metrics are sampled on a timer, so they plot against time.
											</p>
										{/if}
									</div>
									{@render range(
										'x',
										draft.xMin,
										draft.xMax,
										UNITS[axis.kind],
										(v) => set('xMin', v),
										(v) => set('xMax', v)
									)}
									<div class="grid gap-2">
										<Label for="panel-x-label">Label</Label>
										<Input
											id="panel-x-label"
											value={draft.xLabel}
											placeholder={axis.label}
											autocomplete="off"
											oninput={(e) => set('xLabel', e.currentTarget.value)}
										/>
									</div>
								</fieldset>

								{@render tristate(
									'panel-outliers',
									'Ignore outliers',
									draft.ignoreOutliers,
									`Workspace (${onOff(ws.ignoreOutliers)})`,
									(v) => set('ignoreOutliers', v)
								)}
							</div>
						</Tabs.Content>
						<Tabs.Content value="display" class="min-h-0 flex-1 overflow-y-auto px-5 pt-4 pb-6">
							<div class="grid gap-5">
								<div class="grid gap-3">
									<div class="flex items-center justify-between gap-4">
										<Label for="panel-smoothing">Custom smoothing</Label>
										<Switch
											id="panel-smoothing"
											checked={draft.smoothing != null}
											onCheckedChange={(on) => set('smoothing', on ? ws.smoothing : null)}
										/>
									</div>
									<div class="flex items-center gap-3">
										<Slider
											type="single"
											value={draft.smoothing ?? ws.smoothing}
											onValueChange={(v) => set('smoothing', v)}
											min={0}
											max={SMOOTHING_MAX}
											step={0.01}
											disabled={draft.smoothing == null}
											aria-label="Smoothing"
										/>
										<output class="w-9 text-right text-muted-foreground tabular-nums">
											{(draft.smoothing ?? ws.smoothing).toFixed(2)}
										</output>
									</div>
									{#if draft.smoothing == null}
										<p class="text-xs text-muted-foreground">Follows the workspace's settings.</p>
									{/if}
								</div>

								{@render tristate(
									'panel-original',
									'Show original lines',
									draft.showOriginal,
									`Workspace (${onOff(ws.showOriginal)})`,
									(v) => set('showOriginal', v)
								)}

								<div class="grid gap-3">
									<div class="flex items-center justify-between gap-4">
										<Label>Line width</Label>
										{#if draft.lineWidth != null}
											<Button variant="link" size="xs" onclick={() => set('lineWidth', null)}>
												Default
											</Button>
										{/if}
									</div>
									<div class="flex items-center gap-3">
										<Slider
											type="single"
											value={draft.lineWidth ?? 1.25}
											onValueChange={(v) => set('lineWidth', v)}
											min={LINE_WIDTH_MIN}
											max={LINE_WIDTH_MAX}
											step={0.25}
											aria-label="Line width"
										/>
										<output class="w-9 text-right text-muted-foreground tabular-nums">
											{draft.lineWidth == null ? 'Auto' : `${draft.lineWidth}px`}
										</output>
									</div>
								</div>

								<div class="flex items-center justify-between gap-4">
									<Label for="panel-legend">Legend</Label>
									<Select.Root
										type="single"
										value={draft.legend}
										onValueChange={(v) => set('legend', v as Tristate)}
									>
										<Select.Trigger id="panel-legend" size="sm" class="w-40">
											{{ inherit: 'Auto', on: 'Show', off: 'Hide' }[draft.legend]}
										</Select.Trigger>
										<Select.Content>
											<Select.Item value="inherit">Auto</Select.Item>
											<Select.Item value="on">Show</Select.Item>
											<Select.Item value="off">Hide</Select.Item>
										</Select.Content>
									</Select.Root>
								</div>

								<div class="flex items-center justify-between gap-4">
									<Label for="panel-log">Log scale (y)</Label>
									<Switch
										id="panel-log"
										checked={draft.log}
										onCheckedChange={(on) => set('log', on)}
									/>
								</div>
							</div>
						</Tabs.Content>
					</Tabs.Root>
				</aside>
			</div>
			<Dialog.Footer class="m-0 items-center rounded-b-xl sm:justify-between">
				<Button variant="ghost" onclick={() => (draft = draftOf({}, own))}>
					<RotateCcwIcon /> Reset to defaults
				</Button>
				<div class="flex flex-col-reverse gap-2 sm:flex-row">
					<Dialog.Close>
						{#snippet child({ props })}
							<Button {...props} variant="outline">Cancel</Button>
						{/snippet}
					</Dialog.Close>
					<Button disabled={!valid} onclick={apply}>Apply</Button>
				</div>
			</Dialog.Footer>
		{/if}
	</Dialog.Content>
</Dialog.Root>
