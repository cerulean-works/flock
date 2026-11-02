import { beforeEach, describe, expect, it, vi } from 'vitest';
import { authConfig } from '#lib/server/auth.ts';
import { flock, flockAnswer } from '#lib/server/flock.ts';
import type { ProjectInfo, UserInfo } from '#lib/types.ts';
import { load } from './+layout.server.ts';

vi.mock('#lib/server/auth.ts', () => ({ authConfig: vi.fn() }));
vi.mock('#lib/server/flock.ts', () => ({ flock: vi.fn(), flockAnswer: vi.fn() }));

const BERT = {
	entity: 'acme',
	name: 'bert',
	description: null,
	created_at: 1,
	updated_at: 2,
	run_count: 3,
	active_run_count: 0,
	column_count: 0,
	visibility: 'public',
	created_by: null
} satisfies ProjectInfo;

const ME = {
	id: 7,
	email: 'ada@other.test',
	name: 'Ada',
	is_superadmin: false,
	default_org: 'other',
	orgs: [{ name: 'other', display_name: 'Other', role: 'member' }],
	avatar_url: null
} as unknown as UserInfo;

/** Flock's answers by path; anything else is a 404. */
function serve(routes: Record<string, unknown>) {
	const answer = async (path: string) =>
		path in routes ? Response.json(routes[path]) : Response.json({}, { status: 404 });
	vi.mocked(flock).mockImplementation(answer);
	vi.mocked(flockAnswer).mockImplementation(answer);
}

const visit = (params: Record<string, string>, token: string | null) =>
	load({
		params,
		locals: { token },
		cookies: { get: () => undefined }
	} as unknown as Parameters<typeof load>[0]) as Promise<{ shell: Record<string, unknown> }>;

const ACME_OUTSIDE = {
	name: 'acme',
	display_name: 'Acme',
	avatar_url: '/api/v1/avatars/a',
	role: null
};

beforeEach(() => {
	vi.resetAllMocks();
	vi.mocked(authConfig).mockResolvedValue({ signup_open: false, google: false, s3: null });
});

describe('the shell from outside an org', () => {
	it('signed out: the org and its public projects, without asking who is signed in', async () => {
		serve({
			'/api/v1/projects?entity=acme': { projects: [BERT] },
			'/api/v1/orgs/acme': ACME_OUTSIDE
		});
		const { shell } = await visit({ entity: 'acme', project: 'bert' }, null);
		expect(shell).toMatchObject({
			me: null,
			org: 'acme',
			orgName: 'Acme',
			orgAvatar: '/api/v1/avatars/a',
			access: 'public',
			orgs: [],
			projects: [BERT]
		});
		const asked = vi.mocked(flock).mock.calls.map(([path]) => path);
		expect(asked).not.toContain('/api/v1/me');
		expect(asked).not.toContain('/api/v1/orgs');
	});

	it('signed in to another org: the same, with their own orgs to switch to', async () => {
		serve({
			'/api/v1/me': ME,
			'/api/v1/orgs': { orgs: [{ name: 'other', display_name: 'Other', avatar_url: null }] },
			'/api/v1/projects?entity=acme': { projects: [BERT] },
			'/api/v1/orgs/acme': ACME_OUTSIDE
		});
		const { shell } = await visit({ entity: 'acme' }, 'jwt');
		expect(shell).toMatchObject({
			me: { id: 7, name: 'Ada' },
			org: 'acme',
			orgName: 'Acme',
			access: 'public',
			orgs: [{ name: 'other', display_name: 'Other', role: 'member', avatar_url: null }],
			projects: [BERT]
		});
	});

	it('names an org by its ID when it shows nothing to outsiders', async () => {
		serve({ '/api/v1/projects?entity=acme': { projects: [] } });
		const { shell } = await visit({ entity: 'acme' }, null);
		expect(shell).toMatchObject({ orgName: 'acme', orgAvatar: null, access: 'public' });
	});
});

describe('the shell for members', () => {
	it('passes their role down as the access', async () => {
		serve({
			'/api/v1/me': { ...ME, orgs: [{ name: 'acme', display_name: 'Acme', role: 'admin' }] },
			'/api/v1/orgs': { orgs: [{ name: 'acme', display_name: 'Acme', avatar_url: null }] },
			'/api/v1/projects?entity=acme': { projects: [BERT] }
		});
		const { shell } = await visit({ entity: 'acme' }, 'jwt');
		expect(shell).toMatchObject({ access: 'admin', orgName: 'Acme' });
		// A member's org is in their list: there's no outsiders' view of it to read.
		expect(flockAnswer).not.toHaveBeenCalled();
	});
});
