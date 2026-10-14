/**
 * What the page does when an upload is stopped on its way. The upload itself is held by the
 * test: the request and its abort are covered in lib/api-upload.test.ts, the queue in
 * use-uploads.test.ts.
 */
import {
  act,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import { fakeSession, member } from "@/test/auth";
import { fakeDocument, fakeDocuments } from "@/test/ingestion";
import { waitForOnFakeClock } from "@/test/clock";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import * as api from "./documents-api";
import type { StoredDocument } from "./documents-api";
import { CANCEL_RECHECK_MS } from "./step-documents";

vi.mock("./documents-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./documents-api")>()),
  uploadDocument: vi.fn(),
}));

beforeEach(() => {
  // Every byte leaves at once, and the answer never comes: only a cancel ends the upload.
  vi.mocked(api.uploadDocument).mockImplementation(
    (_file, options = {}) =>
      new Promise<StoredDocument>((_resolve, reject) => {
        options.onProgress?.(1);
        options.signal?.addEventListener("abort", () => {
          reject(new ApiError("aborted", "upload cancelled"));
        });
      }),
  );
});

async function open() {
  fakeSession(member);
  const state = fakeDocuments();
  let asked = 0;
  server.use(
    // Counts how often the list is asked, then lets the fake api answer.
    http.get("*/api/ingestion/documents", () => {
      asked += 1;
    }),
  );
  const user = userEvent.setup({ applyAccept: false });
  renderApp("/profile/fill?step=documents");
  await screen.findByRole("heading", { level: 3, name: "Your documents" });
  await waitFor(() => {
    expect(screen.queryByText("Loading your documents")).toBeNull();
  });
  return {
    user,
    state,
    input: screen.getByTestId("document-files"),
    asked: () => asked,
  };
}

const listed = async (name: string) => {
  const list = await screen.findByRole("list", { name: "Your documents" });
  expect(await within(list).findByText(name)).toBeVisible();
};
const announced = () =>
  screen
    .getAllByRole("status")
    .map((region) => region.textContent)
    .join(" | ");

describe("documents: an upload cancelled on its way", () => {
  it("shows the file the server kept anyway, and keeps the keyboard focus", async () => {
    const { user, state, input, asked } = await open();
    await user.upload(input, [
      new File(["every byte left"], "kept anyway.md"),
      new File([], "empty.txt"),
    ]);
    const cancel = await screen.findByRole("button", {
      name: "Cancel kept anyway.md",
    });
    expect(screen.getByText("Finishing")).toBeVisible();
    // The server had the whole body and stored the file; the page stops waiting for its answer.
    state.items.unshift(fakeDocument({ name: "kept anyway.md" }));
    const before = asked();
    cancel.focus();
    await user.keyboard("{Enter}");

    // The row is gone and the focus is on the row that took its place.
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Dismiss empty.txt" }),
      ).toHaveFocus();
    });
    expect(screen.queryByRole("button", { name: /^Cancel/ })).toBeNull();
    // Nothing false is said about the file: only that the page stopped sending.
    expect(announced()).toContain("Stopped sending kept anyway.md.");
    // The list is asked again, and the document shows up without a reload.
    await listed("kept anyway.md");
    expect(asked()).toBe(before + 1);
  });

  it("puts the focus on the button that adds files when no row is left", async () => {
    const { user, input } = await open();
    await user.upload(input, new File(["on its way"], "alone.md"));
    const cancel = await screen.findByRole("button", {
      name: "Cancel alone.md",
    });
    cancel.focus();
    await user.keyboard("{Enter}");
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Choose files" }),
      ).toHaveFocus();
    });
    expect(
      screen.queryByRole("list", { name: "Files being added" }),
    ).toBeNull();
  });

  it("looks once more a moment later, for a file stored after the first look", async () => {
    const { user, state, input, asked } = await open();
    await user.upload(input, new File(["on its way"], "late.md"));
    const cancel = await screen.findByRole("button", {
      name: "Cancel late.md",
    });
    const before = asked();
    // From the click on, the clock of the second look is the test's alone (issue #104): with
    // shouldAdvanceTime the fake clock also ran with the real one, so on a loaded machine the
    // second look could pass before the file below was stored, and nothing listed it any more.
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    try {
      fireEvent.click(cancel);
      // Steps of 1 ms: at most 200 ms of the fake clock, far from the second look.
      await waitForOnFakeClock(() => {
        expect(asked()).toBe(before + 1);
        expect(screen.queryByText("late.md")).toBeNull();
      }, 1);
      // The server finishes storing it after the page's first look.
      state.items.unshift(fakeDocument({ name: "late.md" }));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(CANCEL_RECHECK_MS);
      });
      await waitForOnFakeClock(() => {
        expect(
          within(
            screen.getByRole("list", { name: "Your documents" }),
          ).getByText("late.md"),
        ).toBeVisible();
      });
      expect(asked()).toBe(before + 2);
    } finally {
      vi.useRealTimers();
    }
  });
});
