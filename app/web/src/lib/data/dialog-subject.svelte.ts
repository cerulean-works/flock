/**
 * What a dialog opened from a list acts on: a version, a tag, a trash entry.
 *
 * Closing sets only `open`, and `value` stays until the next `show`, so the dialog's text keeps
 * naming it while the dialog animates closed instead of falling back to a placeholder.
 */
export class DialogSubject<T> {
	open = $state(false);
	value = $state.raw<T | null>(null);

	show = (value: T) => {
		this.value = value;
		this.open = true;
	};
}
