import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { page as screen } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../../routes/layout.css';
import { scrollspy, toc } from '#lib/settings-toc.svelte.ts';
import { page, replaceState, resetApp } from '#lib/testing/app.svelte.ts';
import SettingsNav from './SettingsNav.svelte';

vi.mock('$app/forms', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/navigation', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/state', () => import('#lib/testing/app.svelte.ts'));

const ME = { id: 7, name: 'Ada', email: 'ada@acme.test', is_superadmin: false, avatar_url: null };
const SECTIONS = ['profile', 'organizations', 'keys', 'sign-ins', 'appearance'];

/** The account page's scroller: its sections, each tall enough to scroll past. */
function scroller() {
	const main = document.createElement('main');
	main.style.cssText =
		'position: fixed; top: 0; left: 900px; width: 300px; height: 400px; overflow-y: auto';
	for (const id of SECTIONS) {
		const section = document.createElement('section');
		section.id = id;
		section.dataset.toc = '';
		section.style.height = '500px';
		section.innerHTML = `<h2>${id}</h2>`;
		main.append(section);
	}
	document.body.append(main);
	return main;
}

const entry = (name: string) => screen.getByRole('link', { name, exact: true });

describe('the settings sidebar, as the page’s table of contents', () => {
	let main: HTMLElement;
	let stop: () => void;
	beforeEach(async () => {
		await screen.viewport(1280, 800);
		resetApp('http://flock.test/account', { shell: { me: ME } });
		Object.assign(page, { params: {}, route: { id: '/(app)/account' } });
		main = scroller();
		stop = scrollspy(main);
	});
	afterEach(() => {
		stop();
		main.remove();
		toc.current = null;
	});

	it('links each section to its anchor, and marks the one the page is at', async () => {
		render(SettingsNav, { org: null, orgs: [], superadmin: false, s3: false, item: '' });
		expect(entry('Keys').element().getAttribute('href')).toBe('/account#keys');
		await expect.element(entry('Profile')).toHaveAttribute('aria-current', 'page');
		await expect.element(entry('Keys')).not.toHaveAttribute('aria-current');
	});

	it('scrolls to a section on a click, sets the anchor and marks its entry', async () => {
		const onjump = vi.fn();
		render(SettingsNav, { org: null, orgs: [], superadmin: false, s3: false, item: '', onjump });
		await entry('Keys').click();
		await expect.element(entry('Keys')).toHaveAttribute('aria-current', 'page');
		await expect.element(entry('Profile')).not.toHaveAttribute('aria-current');
		expect(replaceState).toHaveBeenLastCalledWith('/account#keys', expect.anything());
		expect(onjump).toHaveBeenCalledOnce();
		await vi.waitFor(() =>
			expect(document.getElementById('keys')!.getBoundingClientRect().top).toBeLessThan(5)
		);
	});

	it('follows the reader’s own scrolling', async () => {
		render(SettingsNav, { org: null, orgs: [], superadmin: false, s3: false, item: '' });
		main.dispatchEvent(new WheelEvent('wheel'));
		main.scrollTop = document.getElementById('sign-ins')!.offsetTop - 20;
		await expect.element(entry('Sign-ins')).toHaveAttribute('aria-current', 'page');
		expect(replaceState).toHaveBeenLastCalledWith('/account#sign-ins', expect.anything());
	});
});
