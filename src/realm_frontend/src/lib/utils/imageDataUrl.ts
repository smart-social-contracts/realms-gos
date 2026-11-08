// The realm backend refuses a data URL above 1,000,000 characters (IMAGE_MAX in api/setup.py): 700 KiB in base64.
const DEFAULT_MAX_BYTES = 700 * 1024;

function loadImageFromFile(file: File): Promise<HTMLImageElement> {
	return new Promise((resolve, reject) => {
		const url = URL.createObjectURL(file);
		const img = new Image();
		img.onload = () => {
			URL.revokeObjectURL(url);
			resolve(img);
		};
		img.onerror = () => {
			URL.revokeObjectURL(url);
			reject(new Error('Could not read image file'));
		};
		img.src = url;
	});
}

function canvasToBlob(canvas: HTMLCanvasElement, type: string, quality: number): Promise<Blob> {
	return new Promise((resolve, reject) => {
		canvas.toBlob(
			(blob) => {
				if (!blob) reject(new Error('Image encoding failed'));
				else resolve(blob);
			},
			type,
			quality
		);
	});
}

async function encodeCanvas(
	canvas: HTMLCanvasElement,
	maxBytes: number
): Promise<{ dataUrl: string; bytes: number }> {
	const attempts: Array<{ type: string; quality: number; scale: number }> = [];
	for (const scale of [1, 0.85, 0.7, 0.55, 0.4]) {
		for (const quality of [0.92, 0.82, 0.72, 0.62, 0.52]) {
			attempts.push({ type: 'image/jpeg', quality, scale });
		}
	}
	attempts.push({ type: 'image/png', quality: 1, scale: 0.35 });

	const source = canvas;
	const scratch = document.createElement('canvas');
	const ctx = scratch.getContext('2d');
	if (!ctx) throw new Error('Canvas not supported');

	for (const attempt of attempts) {
		scratch.width = Math.max(1, Math.round(source.width * attempt.scale));
		scratch.height = Math.max(1, Math.round(source.height * attempt.scale));
		ctx.clearRect(0, 0, scratch.width, scratch.height);
		ctx.drawImage(source, 0, 0, scratch.width, scratch.height);
		const blob = await canvasToBlob(scratch, attempt.type, attempt.quality);
		if (blob.size <= maxBytes) {
			const dataUrl = await blobToDataUrl(blob);
			return { dataUrl, bytes: blob.size };
		}
	}

	throw new Error('Image is too large even after compression (max 700KB)');
}

function blobToDataUrl(blob: Blob): Promise<string> {
	return new Promise((resolve, reject) => {
		const reader = new FileReader();
		reader.onload = () => resolve(String(reader.result));
		reader.onerror = () => reject(new Error('Failed to encode image'));
		reader.readAsDataURL(blob);
	});
}

/** Read a file as a data URL, downscaling/compressing to stay within maxBytes. */
export async function fileToCompressedDataUrl(
	file: File,
	maxBytes: number = DEFAULT_MAX_BYTES
): Promise<string> {
	if (!file.type.startsWith('image/')) {
		throw new Error('Please choose an image file');
	}

	if (file.size <= maxBytes && (file.type === 'image/jpeg' || file.type === 'image/png')) {
		return blobToDataUrl(file);
	}

	const img = await loadImageFromFile(file);
	const canvas = document.createElement('canvas');
	const longestEdge = Math.max(img.width, img.height, 1);
	const initialScale = longestEdge > 1920 ? 1920 / longestEdge : 1;
	canvas.width = Math.max(1, Math.round(img.width * initialScale));
	canvas.height = Math.max(1, Math.round(img.height * initialScale));
	const ctx = canvas.getContext('2d');
	if (!ctx) throw new Error('Canvas not supported');
	ctx.drawImage(img, 0, 0, canvas.width, canvas.height);

	const { dataUrl } = await encodeCanvas(canvas, maxBytes);
	return dataUrl;
}

const urlCache = new Map<string, string>();

/** Fetch a remote image and encode it as a compressed data URL. */
export async function urlToCompressedDataUrl(
	url: string,
	maxBytes: number = DEFAULT_MAX_BYTES
): Promise<string> {
	const cached = urlCache.get(url);
	if (cached) return cached;

	const res = await fetch(url);
	if (!res.ok) {
		throw new Error(`Could not load image (${res.status})`);
	}
	const blob = await res.blob();
	const type = blob.type.startsWith('image/') ? blob.type : 'image/png';
	const file = new File([blob], 'image', { type });
	const dataUrl = await fileToCompressedDataUrl(file, maxBytes);
	urlCache.set(url, dataUrl);
	return dataUrl;
}

export const SETUP_IMAGE_MAX_BYTES = DEFAULT_MAX_BYTES;
