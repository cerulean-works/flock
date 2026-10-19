import { describe, expect, it } from 'vitest';
import { wandbImport } from './wandb.ts';

const form = (fields: Record<string, string>) => {
	const data = new FormData();
	for (const [k, v] of Object.entries(fields)) data.set(k, v);
	return data;
};

describe('wandbImport', () => {
	it('is null unless the import is switched on', () => {
		expect(wandbImport(form({ wandb_entity: 'acme', wandb_api_key: 'k' }))).toBeNull();
	});
	it('defaults the entity to the organization', () => {
		expect(wandbImport(form({ wandb: 'on', wandb_api_key: ' k ' }), 'acme')).toEqual({
			value: { entity: 'acme', api_key: 'k' }
		});
	});
	it('passes a W&B server when one is given', () => {
		const fields = { wandb: 'on', wandb_entity: 'ml', wandb_api_key: 'k' };
		expect(wandbImport(form({ ...fields, wandb_base_url: 'https://wandb.lab.test' }))).toEqual({
			value: { entity: 'ml', api_key: 'k', base_url: 'https://wandb.lab.test' }
		});
		expect(wandbImport(form({ ...fields, wandb_base_url: 'wandb.lab.test' }))).toEqual({
			error: 'Enter the W&B server as an http(s) URL'
		});
	});
	it('requires a key and an entity', () => {
		expect(wandbImport(form({ wandb: 'on', wandb_entity: 'ml' }))).toEqual({
			error: 'Enter a W&B API key'
		});
		expect(wandbImport(form({ wandb: 'on', wandb_api_key: 'k' }))).toEqual({
			error: 'Enter the W&B entity to import'
		});
	});
});
