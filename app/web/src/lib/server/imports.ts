import { relayAnswer } from './datasets.ts';

/** Flock's routes for an org's Hugging Face imports (D124). */
export const importsPath = (org: string) => `/api/v1/orgs/${encodeURIComponent(org)}/imports/hf`;

/**
 * Pass a request from the browser on to one of the org's import routes, as the signed-in user,
 * with the access token a preview sends in `x-hf-token`.
 */
export const relayImports = (org: string, op: string, request: Request) =>
	relayAnswer(`${importsPath(org)}${op ? `/${op}` : ''}`, request, ['x-hf-token']);
