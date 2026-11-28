import { describe, expect, it } from 'vitest';
import { naturalOrder } from './natural-order.ts';

describe('naturalOrder', () => {
	it('puts losses, errors and times lowest first', () => {
		for (const k of [
			'summary.val/loss',
			'train/loss',
			'suites/zh_tw:bpb',
			'ppl',
			'eval/acc_loss',
			'wer'
		])
			expect(naturalOrder(k, 'num'), k).toBe('asc');
		expect(naturalOrder('runtime', 'duration')).toBe('asc');
	});

	it('puts accuracies and scores highest first', () => {
		for (const k of ['summary.eval/acc', 'mmlu/accuracy', 'reward_mean', 'f1', 'tokens_per_sec'])
			expect(naturalOrder(k, 'num'), k).toBe('desc');
	});

	it('dates newest first, names A to Z, unknown numbers lowest first', () => {
		expect(naturalOrder('created_at', 'time')).toBe('desc');
		expect(naturalOrder('display_name', 'str')).toBe('asc');
		expect(naturalOrder('config.lr', 'num')).toBe('asc');
	});
});
