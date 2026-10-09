import { describe, expect, it, vi } from "vitest";

import {
  checkFile,
  cropRect,
  cropToBlob,
  NOT_AN_IMAGE,
  SOURCE_TOO_LARGE,
  sniffImage,
} from "./crop";

const PNG = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
const JPEG = [0xff, 0xd8, 0xff, 0xe0];

describe("cropRect", () => {
  it("centres the largest square by default", () => {
    expect(cropRect(400, 300, { size: 1, x: 0.5, y: 0.5 })).toEqual({
      sx: 50,
      sy: 0,
      side: 300,
    });
    expect(cropRect(300, 400, { size: 1, x: 0.5, y: 0.5 })).toEqual({
      sx: 0,
      sy: 50,
      side: 300,
    });
  });

  it("is always a square inside the image", () => {
    for (const [w, h] of [
      [4000, 3000],
      [1, 1],
      [7, 1000],
      [1000, 7],
    ] as const) {
      for (const crop of [
        { size: 0.2, x: 0, y: 0 },
        { size: 0.5, x: 1, y: 1 },
        { size: 0.73, x: 0.31, y: 0.9 },
      ]) {
        const r = cropRect(w, h, crop);
        expect(r.side).toBeGreaterThanOrEqual(1);
        expect(r.sx).toBeGreaterThanOrEqual(0);
        expect(r.sy).toBeGreaterThanOrEqual(0);
        expect(r.sx + r.side).toBeLessThanOrEqual(w);
        expect(r.sy + r.side).toBeLessThanOrEqual(h);
      }
    }
  });

  it("clamps out of range and non numeric values", () => {
    expect(cropRect(400, 300, { size: 5, x: -1, y: 9 })).toEqual({
      sx: 0,
      sy: 0,
      side: 300,
    });
    expect(cropRect(400, 300, { size: 0, x: 1, y: 1 })).toEqual({
      sx: 340,
      sy: 240,
      side: 60,
    });
    expect(
      cropRect(400, 300, { size: Number.NaN, x: Number.NaN, y: 0 }).side,
    ).toBe(60);
  });
});

describe("sniffImage and checkFile", () => {
  it("sniffs PNG and JPEG by their bytes only", () => {
    expect(sniffImage(new Uint8Array(PNG))).toBe("image/png");
    expect(sniffImage(new Uint8Array(JPEG))).toBe("image/jpeg");
    expect(sniffImage(new TextEncoder().encode("GIF89a"))).toBeNull();
    expect(sniffImage(new TextEncoder().encode("<svg xmlns"))).toBeNull();
    expect(sniffImage(new Uint8Array([0x89, 0x50]))).toBeNull();
  });

  it("refuses empty, oversized and non image files, whatever their name or type", async () => {
    expect(
      await checkFile(new File([], "a.png", { type: "image/png" })),
    ).toEqual({
      ok: false,
      error: NOT_AN_IMAGE,
    });
    expect(
      await checkFile(new File(["<svg/>"], "a.png", { type: "image/png" })),
    ).toEqual({ ok: false, error: NOT_AN_IMAGE });
    const huge = new File([new Uint8Array(20 * 1024 * 1024 + 1)], "big.jpg");
    expect(await checkFile(huge)).toEqual({
      ok: false,
      error: SOURCE_TOO_LARGE,
    });
  });

  it("accepts a PNG renamed .txt with a text type", async () => {
    const file = new File([new Uint8Array([...PNG, 0, 0])], "photo.txt", {
      type: "text/plain",
    });
    expect(await checkFile(file)).toEqual({ ok: true, mime: "image/png" });
  });
});

function fakeCanvas(
  blob: Blob | null = new Blob(["x"], { type: "image/png" }),
) {
  const context = {
    drawImage: vi.fn(),
    fillRect: vi.fn(),
    fillStyle: "",
    imageSmoothingQuality: "low",
  };
  const canvas = {
    width: 0,
    height: 0,
    getContext: vi.fn(() => context),
    toBlob: vi.fn((cb: (b: Blob | null) => void) => {
      cb(blob);
    }),
  };
  return {
    canvas,
    context,
    create: () => canvas as unknown as HTMLCanvasElement,
  };
}

describe("cropToBlob", () => {
  const source = {} as CanvasImageSource;

  it("draws the selected square onto a square canvas", async () => {
    const { canvas, context, create } = fakeCanvas();
    const blob = await cropToBlob(
      source,
      { sx: 50, sy: 10, side: 300 },
      "image/png",
      create,
    );
    expect(blob.size).toBeGreaterThan(0);
    expect(canvas.width).toBe(300);
    expect(canvas.height).toBe(300);
    expect(context.drawImage).toHaveBeenCalledWith(
      source,
      50,
      10,
      300,
      300,
      0,
      0,
      300,
      300,
    );
    expect(context.fillRect).not.toHaveBeenCalled();
    expect(canvas.toBlob).toHaveBeenCalledWith(
      expect.any(Function),
      "image/png",
      undefined,
    );
  });

  it("caps the output at 1024 pixels and paints JPEG on white", async () => {
    const { canvas, context, create } = fakeCanvas();
    await cropToBlob(
      source,
      { sx: 0, sy: 0, side: 3000 },
      "image/jpeg",
      create,
    );
    expect([canvas.width, canvas.height]).toEqual([1024, 1024]);
    expect(context.fillStyle).toBe("#ffffff");
    expect(context.fillRect).toHaveBeenCalledWith(0, 0, 1024, 1024);
    expect(canvas.toBlob).toHaveBeenCalledWith(
      expect.any(Function),
      "image/jpeg",
      0.92,
    );
  });

  it("rejects when the canvas cannot encode or has no 2d context", async () => {
    await expect(
      cropToBlob(
        source,
        { sx: 0, sy: 0, side: 10 },
        "image/png",
        fakeCanvas(null).create,
      ),
    ).rejects.toThrow("could not be read");
    const { canvas, create } = fakeCanvas();
    canvas.getContext.mockReturnValue(null as never);
    await expect(
      cropToBlob(source, { sx: 0, sy: 0, side: 10 }, "image/png", create),
    ).rejects.toThrow();
  });
});
