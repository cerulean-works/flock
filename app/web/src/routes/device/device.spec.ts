import { beforeEach, describe, expect, it, vi } from 'vitest';
import { error } from '@sveltejs/kit';
import { change, flock } from '#lib/server/flock.ts';
import type { DeviceRequest } from '#lib/types.ts';
import { actions, load } from './+page.server.ts';

vi.mock('#lib/server/flock.ts', () => ({
	change: vi.fn(),
	flock: vi.fn(),
	refused: (res: { status: number }) => res.status >= 400
}));

const visit = (search = '') =>
	({ url: new URL(`https://flock.test/device${search}`) }) as Parameters<typeof load>[0];

const submission = (fields: Record<string, string>) =>
	({
		request: new Request('https://flock.test/device', {
			method: 'POST',
			body: new URLSearchParams(fields)
		})
	}) as Parameters<NonNullable<typeof actions.default>>[0];

const REQUEST: DeviceRequest = {
	user_code: 'WDJB-MJHT',
	hostname: 'node-7',
	platform: 'Linux x86_64',
	ip: '10.0.0.7',
	created_at: 1_790_000_000,
	expires_at: 1_790_000_900
};

beforeEach(() => vi.resetAllMocks());

describe('approving a command-line sign-in', () => {
	it('asks for the code', async () => {
		await expect(load(visit())).resolves.toEqual({ step: 'enter', code: '', error: null });
		expect(flock).not.toHaveBeenCalled();
	});
	it('shows the machine for a code from the link or the form', async () => {
		vi.mocked(flock).mockResolvedValue(Response.json(REQUEST));
		await expect(load(visit('?code=wdjb-mjht'))).resolves.toMatchObject({
			step: 'confirm',
			request: REQUEST
		});
		expect(flock).toHaveBeenCalledWith('/api/v1/auth/device/wdjb-mjht');
	});
	it('goes back to the code with an error for a wrong or used one', async () => {
		vi.mocked(flock).mockImplementation(async () => error(404, 'this code is wrong'));
		await expect(load(visit('?code=BBBB-BBBB'))).resolves.toMatchObject({
			step: 'enter',
			code: 'BBBB-BBBB',
			error: expect.stringContaining('run flock login again')
		});
	});
	it('says when too many codes were wrong', async () => {
		vi.mocked(flock).mockImplementation(async () => error(429, 'too many wrong codes'));
		await expect(load(visit('?code=BBBB-BBBB'))).resolves.toMatchObject({
			step: 'enter',
			error: 'too many wrong codes'
		});
	});
	it('shows the outcome', async () => {
		await expect(load(visit('?done=approved'))).resolves.toEqual({ step: 'done', approved: true });
		await expect(load(visit('?done=denied'))).resolves.toEqual({ step: 'done', approved: false });
	});
	it('authorizes the sign-in', async () => {
		vi.mocked(change).mockResolvedValue({ data: null });
		await expect(
			actions.default(submission({ code: 'WDJB-MJHT', approve: 'true' }))
		).rejects.toMatchObject({ status: 303, location: '/device?done=approved' });
		expect(change).toHaveBeenCalledWith('POST', '/api/v1/auth/device/WDJB-MJHT', {
			approve: true
		});
	});
	it('denies it on cancel', async () => {
		vi.mocked(change).mockResolvedValue({ data: null });
		await expect(
			actions.default(submission({ code: 'WDJB-MJHT', approve: 'false' }))
		).rejects.toMatchObject({ location: '/device?done=denied' });
		expect(change).toHaveBeenCalledWith('POST', '/api/v1/auth/device/WDJB-MJHT', {
			approve: false
		});
	});
	it('explains a sign-in that expired before the decision', async () => {
		vi.mocked(change).mockResolvedValue({ status: 404, data: { error: 'not found' } } as never);
		const result = await actions.default(submission({ code: 'WDJB-MJHT', approve: 'true' }));
		expect(result).toMatchObject({
			status: 404,
			data: { error: expect.stringContaining('run flock login again') }
		});
	});
});
