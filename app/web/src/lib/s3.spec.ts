import { describe, expect, it } from 'vitest';
import {
	bucketNameError,
	ephemeralChoices,
	eventVia,
	expiryChoices,
	groupKeys,
	keySnippets,
	lifetimeLabel
} from './s3.ts';
import type { S3Key } from './types.ts';

const DAY = 86_400;

const key = (id: number, fields: Partial<S3Key> = {}): S3Key => ({
	id,
	name: `key ${id}`,
	access_key_id: `FLK${String(id).padStart(17, 'A')}`,
	org: 'acme',
	read_only: false,
	ephemeral: false,
	session_id: null,
	api_key_id: null,
	source: null,
	created_at: 1_790_000_000,
	expires_at: null,
	last_used_at: null,
	...fields
});

describe('lifetimes', () => {
	it('names them', () => {
		expect(lifetimeLabel(null)).toBe('never');
		expect(lifetimeLabel(8 * 3600)).toBe('8 hours');
		expect(lifetimeLabel(30 * DAY)).toBe('30 days');
		expect(lifetimeLabel(2 * DAY)).toBe('2 days');
		expect(lifetimeLabel(5 * 3600)).toBe('5 hours');
		expect(lifetimeLabel(900)).toBe('15 minutes');
	});

	it('disables expiries past the org’s maximum, with the reason', () => {
		const choices = expiryChoices(30 * DAY);
		expect(choices.map((c) => [c.label, c.disabled === null])).toEqual([
			['8 hours', true],
			['1 day', true],
			['7 days', true],
			['30 days', true],
			['90 days', false],
			['365 days', false],
			['Never', false]
		]);
		expect(choices.at(-1)?.disabled).toBe('This organization’s keys last at most 30 days');
		expect(expiryChoices(null).every((c) => c.disabled === null)).toBe(true);
	});

	it('offers ephemeral lifetimes up to the maximum, and keeps the current one', () => {
		expect(ephemeralChoices(DAY, 8 * 3600).map((c) => c.label)).toEqual([
			'1 hour',
			'4 hours',
			'8 hours',
			'12 hours',
			'1 day'
		]);
		expect(ephemeralChoices(DAY, 2 * 3600).map((c) => c.seconds)).toContain(2 * 3600);
	});
});

describe('the account page’s keys', () => {
	it('folds ephemeral keys per machine and API key', () => {
		const keys = [
			key(1),
			key(2, { ephemeral: true, session_id: 7, source: 'gpu-node-7' }),
			key(3, { ephemeral: true, session_id: 7, source: 'gpu-node-7' }),
			key(4, { ephemeral: true, api_key_id: 9, source: 'ci' }),
			key(5, { ephemeral: true, session_id: 8, source: 'laptop' })
		];
		const { longLived, ephemeral } = groupKeys(keys);
		expect(longLived.map((k) => k.id)).toEqual([1]);
		expect(ephemeral.map((g) => [g.source, g.kind, g.keys.map((k) => k.id)])).toEqual([
			['gpu-node-7', 'machine', [2, 3]],
			['ci', 'api key', [4]],
			['laptop', 'machine', [5]]
		]);
	});

	it('writes snippets with the key', () => {
		const snippets = keySnippets({
			access_key_id: 'FLKAAAAAAAAAAAAAAAAA',
			secret_access_key: 's3cr3t',
			endpoint: 'https://s3.flock.test',
			region: 'us-east-1'
		});
		expect(snippets.map((s) => s.label)).toEqual([
			'AWS CLI profile (~/.aws/config)',
			'boto3',
			'Environment variables'
		]);
		for (const { code } of snippets) {
			expect(code).toContain('FLKAAAAAAAAAAAAAAAAA');
			expect(code).toContain('s3cr3t');
			expect(code).toContain('https://s3.flock.test');
		}
		expect(snippets[0].code).toContain('addressing_style = path');
	});
});

describe('buckets', () => {
	it('checks names as S3 does, within the limit', () => {
		expect(bucketNameError('checkpoints', 57)).toBeNull();
		expect(bucketNameError('a.b-c9', 57)).toBeNull();
		expect(bucketNameError('', 57)).toBe('Enter a name');
		expect(bucketNameError('x'.repeat(58), 57)).toBe('Use at most 57 characters');
		expect(bucketNameError('Upper', 57)).toMatch(/lowercase/);
		expect(bucketNameError('-dash', 57)).toMatch(/lowercase/);
		expect(bucketNameError('a..b', 57)).toBe('Don’t use two dots in a row');
	});

	it('says how events were asked for', () => {
		expect(eventVia({ via: 'cli', machine: 'gpu-node-7' })).toBe(
			'from the command line on gpu-node-7'
		);
		expect(eventVia({ via: 'api', key_name: 'ci' })).toBe('with the API key ci');
		expect(eventVia({ via: 'ui' })).toBe('from the dashboard');
		expect(eventVia({ via: 'purge', objects: 1 })).toBe('emptied (1 object) and removed');
	});
});
