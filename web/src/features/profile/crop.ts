/**
 * The square crop of a profile photo, done in the browser before upload: the user picks a square
 * of the image (size and position), a canvas draws exactly that square, and the result is what
 * gets uploaded (the api re-encodes it again, without metadata).
 */

export const SOURCE_MAX_BYTES = 20 * 1024 * 1024; // what the browser is asked to decode
export const OUTPUT_MAX_SIDE = 1024; // the api keeps at most 1024 px anyway
export const MIN_SIZE = 0.2; // smallest square, as a share of the image's short side

export const NOT_AN_IMAGE = "Choose a PNG or JPEG image.";
export const SOURCE_TOO_LARGE = "Choose an image of at most 20 MB.";
export const UNREADABLE = "This image could not be read. Try another file.";

export interface Crop {
  /** side of the square as a share of the image's short side, MIN_SIZE to 1 */
  size: number;
  /** where the square sits along the free space, 0 (left, top) to 1 (right, bottom) */
  x: number;
  y: number;
}

export const CENTERED: Crop = { size: 1, x: 0.5, y: 0.5 };

export interface Rect {
  sx: number;
  sy: number;
  side: number;
}

const clamp = (value: number, min: number, max: number) =>
  Math.min(max, Math.max(min, Number.isFinite(value) ? value : min));

/** The square, in source pixels, that a crop selects in a width x height image. */
export function cropRect(width: number, height: number, crop: Crop): Rect {
  const short = Math.min(width, height);
  const side = Math.max(1, Math.round(short * clamp(crop.size, MIN_SIZE, 1)));
  return {
    sx: Math.round((width - side) * clamp(crop.x, 0, 1)),
    sy: Math.round((height - side) * clamp(crop.y, 0, 1)),
    side,
  };
}

const PNG = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
const JPEG = [0xff, 0xd8, 0xff];

/** "image/png" or "image/jpeg" from the first bytes, as the api decides; null otherwise. */
export function sniffImage(
  bytes: Uint8Array,
): "image/png" | "image/jpeg" | null {
  const starts = (magic: number[]) => magic.every((b, i) => bytes[i] === b);
  if (starts(PNG)) return "image/png";
  if (starts(JPEG)) return "image/jpeg";
  return null;
}

/** Why a chosen file cannot be used, or its sniffed type. */
export async function checkFile(
  file: Blob,
): Promise<
  { ok: true; mime: "image/png" | "image/jpeg" } | { ok: false; error: string }
> {
  if (file.size === 0) return { ok: false, error: NOT_AN_IMAGE };
  if (file.size > SOURCE_MAX_BYTES)
    return { ok: false, error: SOURCE_TOO_LARGE };
  const head = new Uint8Array(await file.slice(0, 8).arrayBuffer());
  const mime = sniffImage(head);
  return mime ? { ok: true, mime } : { ok: false, error: NOT_AN_IMAGE };
}

export interface LoadedImage {
  image: CanvasImageSource;
  width: number;
  height: number;
  /** an address the page can show (object URL); revoke with `release` */
  url: string;
  release: () => void;
}

/** Decode a file in the browser. Rejects when it is not a readable image. */
export function loadImage(file: Blob): Promise<LoadedImage> {
  const url = URL.createObjectURL(file);
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => {
      resolve({
        image,
        width: image.naturalWidth,
        height: image.naturalHeight,
        url,
        release: () => {
          URL.revokeObjectURL(url);
        },
      });
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error(UNREADABLE));
    };
    image.src = url;
  });
}

/**
 * Draw the selected square onto a square canvas (at most OUTPUT_MAX_SIDE) and encode it: PNG
 * stays PNG (keeps transparency), anything else becomes JPEG.
 */
export function cropToBlob(
  source: CanvasImageSource,
  rect: Rect,
  mime: "image/png" | "image/jpeg",
  createCanvas: () => HTMLCanvasElement = () =>
    document.createElement("canvas"),
): Promise<Blob> {
  const out = Math.min(rect.side, OUTPUT_MAX_SIDE);
  const canvas = createCanvas();
  canvas.width = out;
  canvas.height = out;
  const context = canvas.getContext("2d");
  if (!context) return Promise.reject(new Error(UNREADABLE));
  context.imageSmoothingQuality = "high";
  if (mime === "image/jpeg") {
    // JPEG has no alpha: paint white under any transparent pixel.
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, out, out);
  }
  context.drawImage(
    source,
    rect.sx,
    rect.sy,
    rect.side,
    rect.side,
    0,
    0,
    out,
    out,
  );
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => {
        if (blob) resolve(blob);
        else reject(new Error(UNREADABLE));
      },
      mime,
      mime === "image/jpeg" ? 0.92 : undefined,
    );
  });
}
