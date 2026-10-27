import { beforeEach, describe, expect, it, vi } from 'vitest';
import { redirect } from '@sveltejs/kit';
import { authConfig, needsWorkspace, refreshUser, signIn } from '#lib/server/auth.ts';
import { change } from '#lib/server/flock.ts';
import { actions, load } from './+page.server.ts';

vi.mock('#lib/server/auth.ts', () => ({
	authConfig: vi.fn(),
	needsWorkspace: vi.fn(),
	refreshUser: vi.fn(),
	signIn: vi.fn()
}));
vi.mock('#lib/server/flock.ts', () => ({
	change: vi.fn(),
	refused: (res: { status: number }) => res.status >= 400
}));
vi.mock('#lib/server/session.ts', () => ({ loginHref: () => '/login?next=%2Fsetup' }));

const event = (token: string | null = null) =>
	({ locals: { token }, url: new URL('https://flock.test/setup') }) as Parameters<typeof load>[0];

const submission = (fields: Record<string, string>) =>
	({
		cookies: {},
		request: new Request('https://flock.test/setup', {
			method: 'POST',
			body: new URLSearchParams(fields)
		})
	}) as Parameters<NonNullable<typeof actions.account>>[0];

beforeEach(() => vi.resetAllMocks());

describe('server setup', () => {
	it('starts with the super admin account on an empty server', async () => {
		vi.mocked(authConfig).mockResolvedValue({ signup_open: true, google: true, s3: null });
		await expect(load(event())).resolves.toEqual({ step: 'account', google: true });
	});
	it('resumes at organization creation for a signed-in super admin without an org', async () => {
		vi.mocked(authConfig).mockResolvedValue({ signup_open: false, google: true, s3: null });
		vi.mocked(needsWorkspace).mockResolvedValue(true);
		await expect(load(event('token'))).resolves.toEqual({ step: 'workspace', google: false });
	});
	it('requires sign-in to resume setup', async () => {
		vi.mocked(authConfig).mockResolvedValue({ signup_open: false, google: false, s3: null });
		await expect(load(event())).rejects.toMatchObject({
			status: 303,
			location: '/login?next=%2Fsetup'
		});
	});
	it('leaves setup once no organization step is needed', async () => {
		vi.mocked(authConfig).mockResolvedValue({ signup_open: false, google: false, s3: null });
		vi.mocked(needsWorkspace).mockResolvedValue(false);
		await expect(load(event('token'))).rejects.toMatchObject({ status: 303, location: '/' });
	});
	it('creates only the account before returning to the organization screen', async () => {
		vi.mocked(signIn).mockImplementation(async () => redirect(303, '/setup'));
		await expect(
			actions.account(submission({ name: 'Ana', email: 'ana@lab.test', password: 'password1' }))
		).rejects.toMatchObject({ location: '/setup' });
		expect(signIn).toHaveBeenCalledWith(
			{},
			'/api/v1/auth/signup',
			{ name: 'Ana', email: 'ana@lab.test', password: 'password1' },
			'/setup'
		);
		expect(change).not.toHaveBeenCalled();
	});
	it('keeps an invalid organization ID on the second screen', async () => {
		const fields = { display_name: 'Bad', id: 'bad/name' };
		const result = await actions.workspace(submission(fields));
		expect(result).toMatchObject({ status: 400, data: { displayName: 'Bad', org: 'bad/name' } });
		expect(change).not.toHaveBeenCalled();
	});
	it('asks for a display name', async () => {
		const result = await actions.workspace(submission({ display_name: ' ', id: 'cerulean' }));
		expect(result).toMatchObject({ status: 400, data: { error: 'Enter a display name' } });
		expect(change).not.toHaveBeenCalled();
	});
	it('creates the chosen organization, selects it and opens it', async () => {
		vi.mocked(change).mockResolvedValue({ status: 201, data: {} });
		const fields = { display_name: 'Cerulean Labs', id: 'cerulean' };
		await expect(actions.workspace(submission(fields))).rejects.toMatchObject({
			status: 303,
			location: '/cerulean'
		});
		expect(change).toHaveBeenNthCalledWith(1, 'POST', '/api/v1/orgs', {
			name: 'cerulean',
			display_name: 'Cerulean Labs'
		});
		expect(change).toHaveBeenNthCalledWith(2, 'PATCH', '/api/v1/me', { default_org: 'cerulean' });
		expect(refreshUser).toHaveBeenCalledOnce();
	});
	it('starts a W&B import with the organization and follows it on its settings', async () => {
		vi.mocked(change).mockResolvedValue({ status: 201, data: {} });
		const fields = {
			display_name: 'Cerulean',
			id: 'cerulean',
			wandb: 'on',
			wandb_entity: '',
			wandb_api_key: 'secret'
		};
		await expect(actions.workspace(submission(fields))).rejects.toMatchObject({
			status: 303,
			location: '/orgs/cerulean'
		});
		expect(change).toHaveBeenNthCalledWith(1, 'POST', '/api/v1/orgs', {
			name: 'cerulean',
			display_name: 'Cerulean',
			wandb: { entity: 'cerulean', api_key: 'secret' }
		});
	});
	it('never sends the W&B API key back to the page', async () => {
		vi.mocked(change).mockResolvedValue({
			status: 400,
			data: { error: 'W&B rejected the API key' }
		});
		const fields = {
			display_name: 'Cerulean',
			id: 'cerulean',
			wandb: 'on',
			wandb_entity: 'ml',
			wandb_api_key: 'secret'
		};
		const result = await actions.workspace(submission(fields));
		expect(result).toMatchObject({
			status: 400,
			data: { org: 'cerulean', wandb: true, entity: 'ml', error: 'W&B rejected the API key' }
		});
		expect(JSON.stringify(result)).not.toContain('secret');
		expect(change).toHaveBeenCalledOnce();
	});
	it('asks for a W&B API key before creating anything', async () => {
		const fields = { display_name: 'Cerulean', id: 'cerulean', wandb: 'on' };
		const result = await actions.workspace(submission(fields));
		expect(result).toMatchObject({ status: 400, data: { error: 'Enter a W&B API key' } });
		expect(change).not.toHaveBeenCalled();
	});
});
