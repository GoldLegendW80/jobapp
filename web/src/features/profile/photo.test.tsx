import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { admin, fakeSession } from "@/test/auth";
import { fakeProfile } from "@/test/profile";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import * as crop from "./crop";

// jsdom decodes no images and draws no canvas: the decode and the draw are replaced, the
// checks on the file and the crop maths stay real.
vi.mock("./crop", async (importOriginal) => {
  const real = await importOriginal<typeof import("./crop")>();
  return {
    ...real,
    loadImage: vi.fn(),
    cropToBlob: vi.fn(),
  };
});

const PNG = new Uint8Array([
  0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 1, 2, 3,
]);
const pngFile = (name = "me.png") =>
  new File([PNG], name, { type: "image/png" });
const release = vi.fn();

beforeEach(() => {
  vi.mocked(crop.loadImage).mockResolvedValue({
    image: {} as CanvasImageSource,
    width: 400,
    height: 300,
    url: "blob:photo",
    release,
  });
  vi.mocked(crop.cropToBlob).mockImplementation((_source, rect, mime) =>
    Promise.resolve(new Blob([new Uint8Array(rect.side)], { type: mime })),
  );
});
afterEach(() => {
  toast.dismiss();
  release.mockReset();
});

async function open(options: { photo?: string | null } = {}) {
  fakeSession(admin);
  const state = fakeProfile();
  state.photoEtag = options.photo ?? null;
  const user = userEvent.setup({ applyAccept: false });
  renderApp("/profile");
  await screen.findByRole("heading", { level: 1, name: "Profile" });
  return { user, state, input: screen.getByLabelText("Photo file") };
}

describe("photo", () => {
  it("shows a placeholder without a photo, and the stored one by its ETag", async () => {
    await open();
    expect(
      screen.queryByRole("img", { name: "Your profile photo" }),
    ).toBeNull();
    expect(screen.getByRole("button", { name: "Upload photo" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Remove photo" })).toBeNull();
  });

  it("points the image at the photo with the ETag as cache buster", async () => {
    await open({ photo: '"abc123"' });
    const img = screen.getByRole("img", { name: "Your profile photo" });
    expect(img).toHaveAttribute("src", "/api/profile/photo?v=abc123");
    expect(screen.getByRole("button", { name: "Change photo" })).toBeVisible();
  });

  it("refuses a file that is not a PNG or JPEG before any request", async () => {
    const { user, input, state } = await open();
    await user.upload(
      input,
      new File(["GIF89a..."], "anim.gif", { type: "image/gif" }),
    );
    expect(
      await screen.findByText("Choose a PNG or JPEG image."),
    ).toBeVisible();
    await user.upload(
      input,
      new File(["<svg/>"], "fake.png", { type: "image/png" }),
    );
    expect(
      await screen.findByText("Choose a PNG or JPEG image."),
    ).toBeVisible();
    expect(crop.loadImage).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(state.uploads).toHaveLength(0);
  });

  it("says so when the browser cannot decode the image", async () => {
    vi.mocked(crop.loadImage).mockRejectedValueOnce(new Error(crop.UNREADABLE));
    const { user, input } = await open();
    await user.upload(input, pngFile());
    expect(await screen.findByText(crop.UNREADABLE)).toBeVisible();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("crops a square, uploads it, and shows the stored photo", async () => {
    const { user, input, state } = await open();
    await user.upload(input, pngFile());
    const dialog = await screen.findByRole("dialog", {
      name: "Crop your photo",
    });
    // A 400 x 300 image: the default square is the centred 300 x 300.
    const square = within(dialog).getByTestId("crop-square");
    expect(square.style.left).toBe("12.5%");
    expect(square.style.width).toBe("75%");
    expect(square.style.height).toBe("100%");
    expect(within(dialog).getByText("300 by 300 pixels")).toBeVisible();
    expect(within(dialog).getByLabelText("Vertical position")).toBeDisabled();

    fireEvent.change(within(dialog).getByLabelText("Size"), {
      target: { value: "0.5" },
    });
    fireEvent.change(within(dialog).getByLabelText("Horizontal position"), {
      target: { value: "0" },
    });
    fireEvent.change(within(dialog).getByLabelText("Vertical position"), {
      target: { value: "1" },
    });
    expect(within(dialog).getByText("150 by 150 pixels")).toBeVisible();
    await user.click(
      within(dialog).getByRole("button", { name: "Use this photo" }),
    );

    expect(await screen.findByText("Photo saved.")).toBeVisible();
    expect(crop.cropToBlob).toHaveBeenCalledWith(
      expect.anything(),
      { sx: 0, sy: 150, side: 150 },
      "image/png",
    );
    expect(state.uploads).toHaveLength(1);
    expect(state.uploads[0]!.size).toBe(150);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(release).toHaveBeenCalled();
    expect(
      screen.getByRole("img", { name: "Your profile photo" }),
    ).toHaveAttribute("src", "/api/profile/photo?v=etag-1");
  });

  it("sends the cropped bytes as the raw body with the CSRF header", async () => {
    const seen: { type: string | null; csrf: string | null }[] = [];
    const { user, input } = await open();
    server.use(
      http.put("*/api/profile/photo", ({ request }) => {
        seen.push({
          type: request.headers.get("content-type"),
          csrf: request.headers.get("x-requested-with"),
        });
        return HttpResponse.json({
          mime: "image/jpeg",
          width: 300,
          height: 300,
          size: 300,
          etag: '"e"',
          updated_at: "2026-10-09T08:00:00Z",
        });
      }),
    );
    await user.upload(
      input,
      new File([new Uint8Array([0xff, 0xd8, 0xff, 0xe0])], "a.jpg"),
    );
    const dialog = await screen.findByRole("dialog");
    await user.click(
      within(dialog).getByRole("button", { name: "Use this photo" }),
    );
    await screen.findByText("Photo saved.");
    expect(seen).toEqual([{ type: "image/jpeg", csrf: "jobapp" }]);
  });

  it.each([
    [415, "Only PNG and JPEG images are accepted."],
    [413, "The image is larger than 2 MB."],
    [422, "The image is too large: at most 25 megapixels are accepted."],
  ])(
    "shows the server's %i sentence and keeps the dialog open",
    async (status, detail) => {
      const { user, input } = await open();
      server.use(
        http.put("*/api/profile/photo", () =>
          HttpResponse.json({ detail }, { status }),
        ),
      );
      await user.upload(input, pngFile());
      const dialog = await screen.findByRole("dialog");
      await user.click(
        within(dialog).getByRole("button", { name: "Use this photo" }),
      );
      expect(await within(dialog).findByText(detail)).toBeVisible();
      expect(screen.getByRole("dialog")).toBeVisible();
      expect(
        screen.queryByRole("img", { name: "Your profile photo" }),
      ).toBeNull();
    },
  );

  it("cancel closes the dialog without uploading", async () => {
    const { user, input, state } = await open();
    await user.upload(input, pngFile());
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    expect(release).toHaveBeenCalled();
    expect(state.uploads).toHaveLength(0);
  });

  it("removes the photo", async () => {
    const { user, state } = await open({ photo: '"abc"' });
    await user.click(screen.getByRole("button", { name: "Remove photo" }));
    expect(await screen.findByText("Photo removed.")).toBeVisible();
    expect(state.deletes).toBe(1);
    expect(
      screen.queryByRole("img", { name: "Your profile photo" }),
    ).toBeNull();
    expect(screen.getByRole("button", { name: "Upload photo" })).toBeVisible();
  });

  it("reports a failed removal and keeps the photo", async () => {
    const { user } = await open({ photo: '"abc"' });
    server.use(http.delete("*/api/profile/photo", () => HttpResponse.error()));
    await user.click(screen.getByRole("button", { name: "Remove photo" }));
    expect(
      await screen.findByText(
        "The photo was not removed. The API could not be reached.",
      ),
    ).toBeVisible();
    expect(
      screen.getByRole("img", { name: "Your profile photo" }),
    ).toBeVisible();
  });

  it("does not make the identity form dirty", async () => {
    const { user, input } = await open();
    await user.upload(input, pngFile());
    const dialog = await screen.findByRole("dialog");
    await user.click(
      within(dialog).getByRole("button", { name: "Use this photo" }),
    );
    await screen.findByText("Photo saved.");
    expect(screen.queryByText("Unsaved changes")).toBeNull();
  });
});
