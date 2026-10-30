import { relayAnswer } from './datasets.ts';

/** Flock's routes for an org's models (014). */
export const modelsPath = (org: string) => `/api/v1/orgs/${encodeURIComponent(org)}/models`;

/** Pass a request from the browser on to one of the org's model routes, as the signed-in user. */
export const relayModels = (org: string, op: string, request: Request) =>
	relayAnswer(`${modelsPath(org)}/${op}`, request);
