import { relayAnswer } from './datasets.ts';

/** Flock's routes for an org's evals (026). */
export const evalsPath = (org: string) => `/api/v1/orgs/${encodeURIComponent(org)}/evals`;

/** Pass a request from the browser on to one of the org's eval routes, as the signed-in user. */
export const relayEvals = (org: string, op: string, request: Request) =>
	relayAnswer(`${evalsPath(org)}/${op}`, request);
