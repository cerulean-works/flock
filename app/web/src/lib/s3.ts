/** S3 keys, lifetimes and buckets, as the account and organization pages show them. */

import type { S3Key } from './types.ts';

const HOUR = 3600;
const DAY = 86_400;

/** A lifetime choice: seconds, or null for never. */
export interface Lifetime {
	label: string;
	seconds: number | null;
}

/** Expiries a long-lived key can have (D60). */
export const KEY_EXPIRIES: Lifetime[] = [
	{ label: '8 hours', seconds: 8 * HOUR },
	{ label: '1 day', seconds: DAY },
	{ label: '7 days', seconds: 7 * DAY },
	{ label: '30 days', seconds: 30 * DAY },
	{ label: '90 days', seconds: 90 * DAY },
	{ label: '365 days', seconds: 365 * DAY },
	{ label: 'Never', seconds: null }
];

/** What an org's maximum key lifetime can be set to (D56). */
export const MAX_LIFETIMES: Lifetime[] = KEY_EXPIRIES.filter((l) => l.seconds !== 8 * HOUR);

/** What an org's ephemeral key lifetime can be set to, before the maximum caps it. */
export const EPHEMERAL_LIFETIMES: Lifetime[] = [
	{ label: '1 hour', seconds: HOUR },
	{ label: '4 hours', seconds: 4 * HOUR },
	{ label: '8 hours', seconds: 8 * HOUR },
	{ label: '12 hours', seconds: 12 * HOUR },
	{ label: '1 day', seconds: DAY },
	{ label: '7 days', seconds: 7 * DAY },
	{ label: '30 days', seconds: 30 * DAY }
];

/** `seconds` in words: "8 hours", "30 days", "never" (null). */
export function lifetimeLabel(seconds: number | null): string {
	if (seconds === null) return 'never';
	const preset = [...KEY_EXPIRIES, ...EPHEMERAL_LIFETIMES].find((l) => l.seconds === seconds);
	if (preset) return preset.label;
	if (seconds % DAY === 0) return `${seconds / DAY} days`;
	if (seconds % HOUR === 0) return `${seconds / HOUR} hours`;
	return `${Math.round(seconds / 60)} minutes`;
}

/** Whether a lifetime of `seconds` (null: never) fits under `max` (null: no limit). */
export const fits = (seconds: number | null, max: number | null) =>
	max === null || (seconds !== null && seconds <= max);

/** The expiries a new key for an org with maximum `max` can have, with why others can't. */
export function expiryChoices(max: number | null): (Lifetime & { disabled: string | null })[] {
	return KEY_EXPIRIES.map((l) => ({
		...l,
		disabled: fits(l.seconds, max)
			? null
			: `This organization’s keys last at most ${lifetimeLabel(max)}`
	}));
}

/** The ephemeral lifetimes an org with maximum `max` can choose, keeping `current` listed. */
export function ephemeralChoices(max: number | null, current: number): Lifetime[] {
	const choices = EPHEMERAL_LIFETIMES.filter((l) => fits(l.seconds, max));
	if (!choices.some((l) => l.seconds === current))
		choices.push({ label: lifetimeLabel(current), seconds: current });
	return choices.sort((a, b) => (a.seconds ?? Infinity) - (b.seconds ?? Infinity));
}

/** A machine's (or API key's) ephemeral keys, shown as one line. */
export interface EphemeralGroup {
	/** `session:<id>` or `api-key:<id>`. */
	id: string;
	source: string;
	kind: 'machine' | 'api key';
	keys: S3Key[];
}

/** The keys made in the UI, and the ephemeral ones folded per issuing machine or API key. */
export function groupKeys(keys: S3Key[]): { longLived: S3Key[]; ephemeral: EphemeralGroup[] } {
	const longLived: S3Key[] = [];
	const groups = new Map<string, EphemeralGroup>();
	for (const key of keys) {
		if (!key.ephemeral) {
			longLived.push(key);
			continue;
		}
		const machine = key.session_id !== null;
		const id = machine ? `session:${key.session_id}` : `api-key:${key.api_key_id}`;
		let group = groups.get(id);
		if (!group) {
			group = {
				id,
				source: key.source ?? (machine ? 'a browser' : 'an API key'),
				kind: machine ? 'machine' : 'api key',
				keys: []
			};
			groups.set(id, group);
		}
		group.keys.push(key);
	}
	return { longLived, ephemeral: [...groups.values()] };
}

/** Ready-to-paste ways to use a key. */
export function keySnippets(
	key: { access_key_id: string; secret_access_key: string; endpoint: string; region: string },
	profile = 'flock'
): { label: string; code: string }[] {
	const { access_key_id: id, secret_access_key: secret, endpoint, region } = key;
	return [
		{
			label: 'AWS CLI profile (~/.aws/config)',
			code: [
				`[profile ${profile}]`,
				`aws_access_key_id = ${id}`,
				`aws_secret_access_key = ${secret}`,
				`endpoint_url = ${endpoint}`,
				`region = ${region}`,
				's3 =',
				'    addressing_style = path'
			].join('\n')
		},
		{
			label: 'boto3',
			code: [
				'import boto3',
				'from botocore.config import Config',
				'',
				's3 = boto3.client(',
				'    "s3",',
				`    endpoint_url="${endpoint}",`,
				`    region_name="${region}",`,
				`    aws_access_key_id="${id}",`,
				`    aws_secret_access_key="${secret}",`,
				'    config=Config(s3={"addressing_style": "path"}),',
				')'
			].join('\n')
		},
		{
			label: 'Environment variables',
			code: [
				`export AWS_ACCESS_KEY_ID=${id}`,
				`export AWS_SECRET_ACCESS_KEY=${secret}`,
				`export AWS_ENDPOINT_URL=${endpoint}`,
				`export AWS_REGION=${region}`
			].join('\n')
		}
	];
}

/**
 * Why `name` can't be a bucket name, or null: S3's rules, within `limit` characters (what is
 * left of S3's 63 after the organization's prefix).
 */
export function bucketNameError(name: string, limit: number): string | null {
	if (!name) return 'Enter a name';
	if (name.length > limit) return `Use at most ${limit} characters`;
	if (!/^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$/.test(name))
		return 'Use lowercase letters, digits, hyphens and dots, starting and ending with a letter or digit';
	if (name.includes('..')) return 'Don’t use two dots in a row';
	return null;
}

/** How a bucket event was asked for, in words. */
export function eventVia(detail: {
	via?: string;
	machine?: string;
	key_name?: string;
	objects?: number;
}): string {
	switch (detail.via) {
		case 'ui':
			return 'from the dashboard';
		case 'cli':
			return detail.machine
				? `from the command line on ${detail.machine}`
				: 'from the command line';
		case 'api':
			return detail.key_name ? `with the API key ${detail.key_name}` : 'through the API';
		case 'org':
			return 'with its organization';
		case 'flock':
			return 'by Flock';
		case 'purge':
			return detail.objects === undefined
				? 'emptied and removed'
				: `emptied (${detail.objects.toLocaleString('en-US')} ${detail.objects === 1 ? 'object' : 'objects'}) and removed`;
		default:
			return '';
	}
}
