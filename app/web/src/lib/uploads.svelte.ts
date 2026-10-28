/**
 * The file browser's upload queue (D65, D69). Bytes go straight from the browser to the S3
 * proxy by presigned URL: one PUT up to 100 MiB, S3 multipart above (16 MiB parts, four at a
 * time), up to 50 GiB a file.
 */

import { SINGLE_PUT_LIMIT, UPLOAD_LIMIT, bucketCall } from './storage.ts';
import type { PresignRequest, Presigned } from './types.ts';
import { bytes } from './utils.ts';

/** Parts sent at once, and signed per request (their URLs last 15 minutes). */
const PARALLEL_PARTS = 4;
/** Files uploading at once. */
const PARALLEL_FILES = 3;

/** How the queue reaches Flock and the proxy; tests give it a fake. */
export interface Transport {
	presign(body: PresignRequest): Promise<Presigned>;
	/** PUT `body` to a presigned URL, reporting bytes sent; returns the ETag. */
	put(
		url: string,
		body: Blob,
		type: string | null,
		progress: (sent: number) => void,
		signal: AbortSignal
	): Promise<string | null>;
	/** POST a CompleteMultipartUpload body to a presigned URL. */
	complete(url: string, xml: string, signal: AbortSignal): Promise<void>;
}

export type UploadState = 'queued' | 'uploading' | 'done' | 'failed' | 'cancelled';

let uploads = 0;

export class Upload {
	readonly id = ++uploads;
	sent = $state(0);
	state = $state<UploadState>('queued');
	error = $state<string | null>(null);
	readonly controller = new AbortController();

	constructor(
		readonly key: string,
		readonly file: Blob
	) {}

	get size() {
		return this.file.size;
	}

	get multipart() {
		return this.size > SINGLE_PUT_LIMIT;
	}

	cancel() {
		if (this.state === 'queued' || this.state === 'uploading') {
			this.state = 'cancelled';
			this.controller.abort();
		}
	}
}

/** Thrown when an upload is cancelled midway. */
class Cancelled extends Error {}

export class UploadQueue {
	uploads = $state<Upload[]>([]);
	#transport: Transport;
	#onsettled: () => void;
	#running = 0;

	/** `onsettled` runs whenever the queue has nothing left to do (to refresh the listing). */
	constructor(transport: Transport, onsettled: () => void = () => {}) {
		this.#transport = transport;
		this.#onsettled = onsettled;
	}

	get active() {
		return this.uploads.filter((u) => u.state === 'queued' || u.state === 'uploading');
	}

	/** Queue `files`, each under its `key`. */
	add(files: { key: string; file: Blob }[]) {
		for (const { key, file } of files) {
			const upload = new Upload(key, file);
			if (file.size > UPLOAD_LIMIT) {
				upload.state = 'failed';
				upload.error = `Files over ${bytes(UPLOAD_LIMIT)} have to be uploaded with aws s3 cp.`;
			}
			this.uploads.push(upload);
		}
		this.#pump();
	}

	/** Forget finished, failed and cancelled uploads. */
	clear() {
		this.uploads = this.active;
	}

	#pump() {
		while (this.#running < PARALLEL_FILES) {
			const next = this.uploads.find((u) => u.state === 'queued');
			if (!next) break;
			this.#running++;
			next.state = 'uploading';
			void this.#run(next).finally(() => {
				this.#running--;
				this.#pump();
				if (this.#running === 0) this.#onsettled();
			});
		}
	}

	async #run(upload: Upload) {
		try {
			if (upload.multipart) await this.#multipart(upload);
			else await this.#single(upload);
			if (upload.state === 'cancelled') return; // too late: it had already gone through
			upload.sent = upload.size;
			upload.state = 'done';
		} catch (e) {
			if (upload.state === 'cancelled' || e instanceof Cancelled) {
				upload.state = 'cancelled';
				return;
			}
			upload.state = 'failed';
			upload.error = e instanceof Error ? e.message : String(e);
		}
	}

	async #single(upload: Upload) {
		const { url } = await this.#transport.presign({
			method: 'put',
			key: upload.key,
			size: upload.size
		});
		await this.#transport.put(
			url!,
			upload.file,
			upload.file.type || null,
			(sent) => (upload.sent = sent),
			upload.controller.signal
		);
	}

	async #multipart(upload: Upload) {
		const t = this.#transport;
		const { key } = upload;
		const started = await t.presign({
			method: 'multipart',
			key,
			size: upload.size,
			content_type: upload.file.type || undefined
		});
		const uploadId = started.upload_id!;
		const partSize = started.part_size!;
		const count = started.part_count!;
		const sent = new Array<number>(count).fill(0);
		const etags: string[] = [];
		try {
			for (let first = 1; first <= count; first += PARALLEL_PARTS) {
				if (upload.controller.signal.aborted) throw new Cancelled();
				const numbers = Array.from(
					{ length: Math.min(PARALLEL_PARTS, count - first + 1) },
					(_, i) => first + i
				);
				const { parts } = await t.presign({
					method: 'parts',
					key,
					upload_id: uploadId,
					parts: numbers
				});
				await Promise.all(
					numbers.map(async (n) => {
						const chunk = upload.file.slice((n - 1) * partSize, n * partSize);
						const etag = await t.put(
							parts![String(n)],
							chunk,
							null,
							(bytes) => {
								sent[n - 1] = bytes;
								upload.sent = sent.reduce((a, b) => a + b, 0);
							},
							upload.controller.signal
						);
						if (!etag) throw new Error('The S3 proxy didn’t return the part’s ETag');
						etags[n - 1] = etag;
					})
				);
			}
			if (upload.controller.signal.aborted) throw new Cancelled();
			const { url } = await t.presign({ method: 'complete', key, upload_id: uploadId });
			await t.complete(url!, completion(etags), upload.controller.signal);
		} catch (e) {
			// Parts already stored would otherwise wait on the S3 server for nothing.
			await t.presign({ method: 'abort', key, upload_id: uploadId }).catch(() => {});
			throw e;
		}
	}
}

/** The body of CompleteMultipartUpload for parts 1… with these ETags. */
export function completion(etags: string[]): string {
	const parts = etags
		.map(
			(etag, i) => `<Part><PartNumber>${i + 1}</PartNumber><ETag>${escapeXml(etag)}</ETag></Part>`
		)
		.join('');
	return `<CompleteMultipartUpload>${parts}</CompleteMultipartUpload>`;
}

const escapeXml = (s: string) =>
	s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

/** An S3 error body's message, if it is one. */
export function s3ErrorMessage(body: string): string | null {
	const code = /<Code>([^<]*)<\/Code>/.exec(body)?.[1];
	const message = /<Message>([^<]*)<\/Message>/.exec(body)?.[1];
	if (!code && !message) return null;
	return message || code || null;
}

/** Why a request to the proxy at `url` failed without an answer. */
export const unreachable = (url: string) =>
	`Couldn’t reach the S3 proxy at ${/^[a-z]+:\/\/[^/?#]+/i.exec(url)?.[0] ?? url}.`;

/** A file to upload, with its path under the folder it goes in. */
export interface Picked {
	path: string;
	file: File;
}

/** Files picked in a file input: a folder picked keeps its files' relative paths. */
export const pickedFiles = (files: FileList): Picked[] =>
	[...files].map((file) => ({ path: file.webkitRelativePath || file.name, file }));

/** What was dropped: files, and folders' files with their paths under the folder. */
export async function droppedFiles(data: DataTransfer): Promise<Picked[]> {
	// Entries must be taken before anything is awaited: the drop's items go away after it.
	const entries = [...data.items]
		.filter((item) => item.kind === 'file')
		.map((item) => item.webkitGetAsEntry?.() ?? null);
	if (entries.some((e) => e === null)) return pickedFiles(data.files);
	const out: Picked[] = [];
	async function walk(entry: FileSystemEntry, path: string): Promise<void> {
		if (entry.isFile) {
			const file = await new Promise<File>((ok, fail) =>
				(entry as FileSystemFileEntry).file(ok, fail)
			);
			out.push({ path: path + entry.name, file });
			return;
		}
		const reader = (entry as FileSystemDirectoryEntry).createReader();
		// A directory's entries come in batches, until an empty one.
		for (;;) {
			const batch = await new Promise<FileSystemEntry[]>((ok, fail) =>
				reader.readEntries(ok, fail)
			);
			if (!batch.length) break;
			for (const child of batch) await walk(child, `${path}${entry.name}/`);
		}
	}
	for (const entry of entries) await walk(entry!, '');
	return out;
}

/** The real transport: Flock through the dashboard's endpoints, the proxy by XHR. */
export function browserTransport(org: string, bucket: string): Transport {
	return {
		presign: (body) => bucketCall<Presigned>(org, bucket, 'presign', body),
		put(url, body, type, progress, signal) {
			return new Promise((resolve, reject) => {
				const xhr = new XMLHttpRequest();
				xhr.open('PUT', url);
				if (type) xhr.setRequestHeader('content-type', type);
				xhr.upload.onprogress = (e) => progress(e.loaded);
				xhr.onload = () => {
					if (xhr.status >= 200 && xhr.status < 300) resolve(xhr.getResponseHeader('etag'));
					else
						reject(
							new Error(s3ErrorMessage(xhr.responseText) ?? `The S3 proxy answered ${xhr.status}`)
						);
				};
				xhr.onerror = () => reject(new Error(unreachable(url)));
				xhr.onabort = () => reject(new Cancelled());
				signal.addEventListener('abort', () => xhr.abort(), { once: true });
				xhr.send(body);
			});
		},
		async complete(url, xml, signal) {
			let res: Response;
			try {
				res = await fetch(url, {
					method: 'POST',
					headers: { 'content-type': 'application/xml' },
					body: xml,
					signal
				});
			} catch (e) {
				if (signal.aborted) throw new Cancelled();
				throw new Error(unreachable(url), { cause: e });
			}
			// CompleteMultipartUpload can fail after a 200, with an error body.
			const text = await res.text();
			const failed = /<Error>/.test(text) ? s3ErrorMessage(text) : null;
			if (!res.ok || failed)
				throw new Error(failed ?? s3ErrorMessage(text) ?? `The S3 proxy answered ${res.status}`);
		}
	};
}
