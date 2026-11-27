/**
 * The properties pane's state, shared by every page that has one (a dataset, a model, an object,
 * a run): hidden on one, it stays hidden on the next, as Linear's does. Kept for the session.
 */
export const pane = $state({ open: true });
