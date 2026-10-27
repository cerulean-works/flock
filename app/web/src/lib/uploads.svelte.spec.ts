import { describe, expect, it, vi } from 'vitest';
import { SINGLE_PUT_LIMIT } from './storage.ts';
import type { PresignRequest, Presigned } from './types.ts';
import { UploadQueue, completion, s3ErrorMessage, type Transport } from './uploads.svelte.ts';

const MIB = 1024 * 1024;

/** A transport recording what the queue asks for; parts' ETags are `"etag-{n}"`. */
function fake(fail?: (url: string) => boolean, delay = 5) {
	const asked: PresignRequest[] = [];
	const puts: { url: string; size: number }[] = [];
	const completed: string[] = [];
	const transport: Transport = {
		async presign(body) {
			asked.push(body);
			const out: Presigned = {};
			if (body.method === 'put') out.url = `put:${body.key}`;
			if (body.method === 'multipart')
				Object.assign(out, {
					upload_id: 'u1',
					part_size: 16 * MIB,
					part_count: Math.ceil(body.size / (16 * MIB))
				});
			if (body.method === 'parts')
				out.parts = Object.fromEntries(body.parts.map((n) => [String(n), `part:${n}`]));
			if (body.method === 'complete') out.url = 'complete';
			return out;
		},
		async put(url, blob, _type, progress, signal) {
			await new Promise((r) => setTimeout(r, delay));
			if (signal.aborted) throw new Error('aborted');
			if (fail?.(url)) throw new Error('The S3 proxy answered 500');
			progress(blob.size);
			puts.push({ url, size: blob.size });
			return url.startsWith('part:') ? `"etag-${url.slice(5)}"` : '"etag"';
		},
		async complete(_url, xml) {
			completed.push(xml);
		}
	};
	return { transport, asked, puts, completed };
}

const settled = (queue: UploadQueue) => vi.waitFor(() => expect(queue.active).toEqual([]));

describe('the upload queue', () => {
	it('uploads small files with one presigned PUT each', async () => {
		const { transport, asked, puts } = fake();
		const onsettled = vi.fn();
		const queue = new UploadQueue(transport, onsettled);
		queue.add([
			{ key: 'runs/a.txt', file: new Blob(['hello']) },
			{ key: 'runs/b.txt', file: new Blob(['hi']) }
		]);
		await settled(queue);
		expect(asked).toEqual([
			{ method: 'put', key: 'runs/a.txt', size: 5 },
			{ method: 'put', key: 'runs/b.txt', size: 2 }
		]);
		expect(puts.map((p) => p.url)).toEqual(['put:runs/a.txt', 'put:runs/b.txt']);
		expect(queue.uploads.map((u) => [u.state, u.sent])).toEqual([
			['done', 5],
			['done', 2]
		]);
		expect(onsettled).toHaveBeenCalledTimes(1);
	});

	it('uploads large files in parts, four at a time, and completes them in order', async () => {
		const { transport, asked, puts, completed } = fake();
		const queue = new UploadQueue(transport);
		const size = SINGLE_PUT_LIMIT + MIB; // 101 MiB: seven 16 MiB parts
		queue.add([{ key: 'big.bin', file: new Blob([new Uint8Array(size)]) }]);
		await settled(queue);
		expect(asked.map((a) => a.method)).toEqual(['multipart', 'parts', 'parts', 'complete']);
		expect(asked[1]).toMatchObject({ parts: [1, 2, 3, 4], upload_id: 'u1' });
		expect(asked[2]).toMatchObject({ parts: [5, 6, 7] });
		expect(puts.map((p) => p.size)).toEqual([...Array(6).fill(16 * MIB), 5 * MIB]);
		expect(completed).toEqual([completion(Array.from({ length: 7 }, (_, i) => `"etag-${i + 1}"`))]);
		expect(queue.uploads[0].state).toBe('done');
		expect(queue.uploads[0].sent).toBe(size);
	});

	it('aborts a multipart upload when a part fails', async () => {
		const { transport, asked } = fake((url) => url === 'part:2');
		const queue = new UploadQueue(transport);
		queue.add([{ key: 'big.bin', file: new Blob([new Uint8Array(SINGLE_PUT_LIMIT + 1)]) }]);
		await settled(queue);
		expect(asked.at(-1)).toEqual({ method: 'abort', key: 'big.bin', upload_id: 'u1' });
		expect(queue.uploads[0].state).toBe('failed');
		expect(queue.uploads[0].error).toBe('The S3 proxy answered 500');
	});

	it('cancels, and aborts what was started', async () => {
		const { transport, asked } = fake(undefined, 200);
		const queue = new UploadQueue(transport);
		queue.add([{ key: 'big.bin', file: new Blob([new Uint8Array(SINGLE_PUT_LIMIT + 1)]) }]);
		await vi.waitFor(() => expect(asked.some((a) => a.method === 'parts')).toBe(true));
		queue.uploads[0].cancel();
		await settled(queue);
		expect(queue.uploads[0].state).toBe('cancelled');
		await vi.waitFor(() => expect(asked.at(-1)?.method).toBe('abort'));
	});

	it('refuses files over 50 GiB, for the command line', () => {
		const { transport, asked } = fake();
		const queue = new UploadQueue(transport);
		const huge = { size: 51 * 1024 ** 3, type: '', slice: () => new Blob() } as unknown as Blob;
		queue.add([{ key: 'huge.bin', file: huge }]);
		expect(queue.uploads[0].state).toBe('failed');
		expect(queue.uploads[0].error).toMatch(/command line/);
		expect(asked).toEqual([]);
	});

	it('clears finished uploads, keeping running ones', async () => {
		const { transport } = fake((url) => url === 'put:bad');
		const queue = new UploadQueue(transport);
		queue.add([
			{ key: 'ok', file: new Blob(['1']) },
			{ key: 'bad', file: new Blob(['2']) }
		]);
		await settled(queue);
		expect(queue.uploads.map((u) => u.state)).toEqual(['done', 'failed']);
		queue.clear();
		expect(queue.uploads).toEqual([]);
	});
});

describe('S3 answers', () => {
	it('reads the message of an error body', () => {
		expect(
			s3ErrorMessage('<Error><Code>AccessDenied</Code><Message>Access Denied</Message></Error>')
		).toBe('Access Denied');
		expect(s3ErrorMessage('<ok/>')).toBeNull();
	});
	it('escapes ETags in the completion', () => {
		expect(completion(['"a"'])).toBe(
			'<CompleteMultipartUpload><Part><PartNumber>1</PartNumber><ETag>&quot;a&quot;</ETag></Part></CompleteMultipartUpload>'
		);
	});
});
