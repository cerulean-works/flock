import { beforeEach, describe, expect, it, vi } from 'vitest';
import { page as screen } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../../routes/layout.css';
import { page, resetApp } from '#lib/testing/app.svelte.ts';
import type { ProjectInfo } from '#lib/types.ts';
import Sidebar from './Sidebar.svelte';

vi.mock('$app/forms', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/navigation', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/state', () => import('#lib/testing/app.svelte.ts'));

const BERT: ProjectInfo = {
	entity: 'acme',
	name: 'bert',
	description: null,
	created_at: 1,
	updated_at: 2,
	run_count: 3,
	active_run_count: 0,
	column_count: 0,
	visibility: 'public'
};

const ADA = { id: 7, name: 'Ada', email: 'ada@other.test', is_superadmin: false, avatar_url: null };
const OTHER = { name: 'other', display_name: 'Other', role: 'member' as const, avatar_url: null };

/** A project page of acme, seen by `me` (null: signed out). */
function open(me: typeof ADA | null) {
	resetApp('http://flock.test/acme/bert?view=charts', { shell: { me }, user: me });
	Object.assign(page, {
		params: { entity: 'acme', project: 'bert' },
		route: { id: '/(app)/[entity]/[project]' }
	});
}

const link = (name: string) => screen.getByRole('link', { name, exact: true });

describe('the sidebar from outside the org', () => {
	beforeEach(() => open(null));

	it('signed out: the org, its public projects, and a Sign in button', async () => {
		render(Sidebar, {
			org: 'acme',
			orgs: [],
			projects: [BERT],
			s3: true,
			access: 'public',
			orgName: 'Acme',
			orgAvatar: null,
			signedIn: false
		});
		await expect.element(link('Acme')).toBeVisible();
		expect(link('Acme').element().getAttribute('href')).toBe('/acme');
		await expect.element(screen.getByRole('heading', { name: 'Public projects' })).toBeVisible();
		await expect.element(link('bert')).toBeVisible();
		await expect.element(link('Sign in')).toBeVisible();
		expect(link('Sign in').element().getAttribute('href')).toBe(
			'/login?next=%2Facme%2Fbert%3Fview%3Dcharts'
		);
		// None of the members' pages, nothing to change, and nobody's account.
		for (const name of ['Home', 'Runs', 'Datasets', 'Models', 'Storage'])
			await expect.element(link(name)).not.toBeInTheDocument();
		await expect
			.element(screen.getByRole('button', { name: 'New project' }))
			.not.toBeInTheDocument();
		await expect
			.element(screen.getByRole('button', { name: 'bert actions' }))
			.not.toBeInTheDocument();
	});

	it('signed in elsewhere: the same, with their account menu and orgs', async () => {
		open(ADA);
		render(Sidebar, {
			org: 'acme',
			orgs: [OTHER],
			projects: [BERT],
			s3: true,
			access: 'public',
			orgName: 'Acme',
			orgAvatar: null,
			signedIn: true
		});
		await expect.element(link('Acme')).toBeVisible();
		await expect.element(link('bert')).toBeVisible();
		await expect.element(link('Home')).toBeVisible();
		for (const name of ['Runs', 'Datasets', 'Models', 'Storage', 'Sign in'])
			await expect.element(link(name)).not.toBeInTheDocument();
		// The switcher names their own orgs, not the one visited.
		const menu = screen.getByRole('button', { name: 'No organization · Ada' });
		await expect.element(menu).toBeVisible();
		await expect.element(menu.getByText('ada@other.test')).toBeVisible();
		await menu.click();
		await expect.element(screen.getByRole('menuitem', { name: 'Other' })).toBeVisible();
		await expect.element(screen.getByRole('menuitem', { name: 'Sign out' })).toBeVisible();
	});
});

describe('the sidebar for members', () => {
	it('has the org’s pages and its project actions', async () => {
		open(ADA);
		render(Sidebar, {
			org: 'acme',
			orgs: [{ ...OTHER, name: 'acme', display_name: 'Acme' }],
			projects: [BERT],
			s3: true,
			access: 'member',
			signedIn: true
		});
		for (const name of ['Home', 'Runs', 'Datasets', 'Models', 'Storage'])
			await expect.element(link(name)).toBeVisible();
		await expect.element(screen.getByRole('heading', { name: 'Projects' })).toBeVisible();
		await expect.element(screen.getByRole('button', { name: 'New project' })).toBeVisible();
		await expect.element(screen.getByRole('button', { name: 'bert actions' })).toBeInTheDocument();
		await expect.element(link('Sign in')).not.toBeInTheDocument();
	});
});
