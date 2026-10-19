import { invalidate } from '$app/navigation';
import { formText } from './utils.ts';

/** What the org page's load depends on: polling invalidates it while an import runs. */
export const IMPORT = 'flock:org-import';

/** Re-run the page's load this often while an import runs and the tab is visible. */
export function pollImport(ms = 3_000) {
	const id = setInterval(() => {
		if (document.visibilityState === 'visible') invalidate(IMPORT);
	}, ms);
	return () => clearInterval(id);
}

/** What Flock takes to copy a W&B entity into an organization. */
export interface WandbImport {
	entity: string;
	api_key: string;
	base_url?: string;
}

/** The form fields of `WandbImportFields`. */
export const WANDB_FIELDS = {
	on: 'wandb',
	entity: 'wandb_entity',
	key: 'wandb_api_key',
	url: 'wandb_base_url'
} as const;

/**
 * The W&B import a form asks for: `null` when it doesn't, else the import or why it can't be
 * sent. The entity defaults to `entity` (the organization's own name), as wandb entities and
 * Flock organizations usually share it.
 */
export function wandbImport(
	form: FormData,
	entity = ''
): { value: WandbImport } | { error: string } | null {
	if (form.get(WANDB_FIELDS.on) !== 'on') return null;
	const chosen = formText(form, WANDB_FIELDS.entity) || entity;
	const api_key = formText(form, WANDB_FIELDS.key);
	const base_url = formText(form, WANDB_FIELDS.url);
	if (!chosen) return { error: 'Enter the W&B entity to import' };
	if (!api_key) return { error: 'Enter a W&B API key' };
	if (base_url && !/^https?:\/\/\S+$/.test(base_url))
		return { error: 'Enter the W&B server as an http(s) URL' };
	return { value: { entity: chosen, api_key, ...(base_url ? { base_url } : {}) } };
}
