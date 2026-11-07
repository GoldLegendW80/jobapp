import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { MemoryRouter, useLocation } from "react-router";
import {
  act,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { toast } from "sonner";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fakeSession, member } from "@/test/auth";
import { fakeCv, fakeDocuments } from "@/test/documents";
import {
  fakeDocuments as fakeFiles,
  LIMITS,
  type FakeDocuments as FakeFiles,
} from "@/test/ingestion";
import { fakeRuns } from "@/test/ingestion-runs";
import { fakeProfile } from "@/test/profile";
import { renderApp, renderWithQuery } from "@/test/render";
import { server } from "@/test/server";

import type { BaseCv } from "@/features/documents/api/base-cvs-api";
import type { DocumentStatus } from "@/features/ingestion/api/documents-api";
import { RUN_POLL_MS } from "@/features/ingestion/api/runs-api";

import {
  FILL_REVIEW,
  NOT_A_CV,
  READING_FOR_PROFILE,
  REVIEW_OPENED,
} from "@/features/documents/helpers/upload-cv";
import { UploadPanel } from "@/components/shared/documents/upload-panel";

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

/** The ingestion consent of a Fill my profile run, as the api answers it. */
const RUN_CONSENT = {
  version: 1,
  text: "Your selected documents are sent to Anthropic to complete your profile (an invented test text).",
  reminder: "Your documents are sent to Anthropic, as you agreed.",
  accepted: false,
  accepted_at: null,
};

/** Where the panel's router is now. */
function Where() {
  const where = useLocation();
  return <p data-testid="where">{`${where.pathname}${where.search}`}</p>;
}

/** A separator dash: an em dash, an en dash, or a hyphen between spaces. */
const SEPARATOR = /[–—]| - /;
const PDF = new File(["%PDF-1.7 Morgan Exampleton"], "Morgan CV.pdf", {
  type: "application/pdf",
});

/** The uploaded files as the list answers them, every one at `status`. */
function filesAre(files: FakeFiles, status: DocumentStatus, extra = {}) {
  server.use(
    http.get("*/api/ingestion/documents", () =>
      HttpResponse.json({
        items: files.items.map((one) => ({ ...one, status, ...extra })),
        usage: { bytes: 0, documents: files.items.length },
        limits: LIMITS,
      }),
    ),
  );
}

/** The conversion answers `made`, and the list then holds it. */
function convertsInto(
  api: ReturnType<typeof fakeDocuments>,
  made: Partial<BaseCv>,
) {
  const cv = fakeCv({
    id: "cv-up",
    name: "Morgan CV",
    source: "uploaded",
    is_default: false,
    ...made,
  });
  server.use(
    http.post("*/api/documents/base-cvs", async ({ request }) => {
      api.requests.push({
        method: "POST",
        path: "/api/documents/base-cvs",
        body: await request.json(),
        headers: request.headers,
      });
      api.cvs.push(cv);
      return HttpResponse.json({ base_cv: cv }, { status: 202 });
    }),
  );
  return cv;
}

function panel(options: Parameters<typeof userEvent.setup>[0] = {}) {
  fakeSession(member);
  const user = userEvent.setup(options);
  const view = renderWithQuery(
    <MemoryRouter>
      <UploadPanel openHref={(id) => `/somewhere/${id}`} />
      <Where />
    </MemoryRouter>,
  );
  return { user, ...view };
}

const consents = (api: ReturnType<typeof fakeDocuments>) =>
  api.requests
    .filter(
      (one) => one.method === "POST" && one.path === "/api/documents/consent",
    )
    .map((one) => one.body);

const conversions = (api: ReturnType<typeof fakeDocuments>) =>
  api.requests.filter(
    (one) => one.method === "POST" && one.path === "/api/documents/base-cvs",
  );

describe("the upload panel", () => {
  it("sends a PDF through ingestion and says it is reading it", async () => {
    const api = fakeDocuments({ consent: { accepted: true } });
    const files = fakeFiles();
    const { user } = panel();
    await user.upload(screen.getByTestId("upload-cv-file"), PDF);
    expect(await screen.findByText("Reading your CV")).toBeVisible();
    expect(files.uploads).toHaveLength(1);
    expect(files.uploads[0]?.filename).toBe("UTF-8''Morgan%20CV.pdf");
    // The server keeps the file of a CV sent here: the design capture reads it.
    expect(files.uploads[0]?.purpose).toBe("base_cv");
    expect(conversions(api)).toEqual([]); // the text is not read yet
  });

  it("takes a Word document dropped on it", async () => {
    fakeDocuments({ consent: { accepted: true } });
    const files = fakeFiles();
    panel();
    const docx = new File(["PK Morgan Exampleton"], "Morgan CV.docx", {
      type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    });
    const zone = screen.getByTestId("upload-drop");
    fireEvent.dragOver(zone, { dataTransfer: { files: [docx] } });
    expect(zone).toHaveAttribute("data-over", "true");
    fireEvent.drop(zone, { dataTransfer: { files: [docx] } });
    expect(zone).toHaveAttribute("data-over", "false");
    await waitFor(() => {
      expect(files.uploads.map((one) => one.filename)).toEqual([
        "UTF-8''Morgan%20CV.docx",
      ]);
    });
  });

  it("refuses a file that is not a PDF or a Word document before sending it", async () => {
    fakeDocuments();
    const files = fakeFiles();
    const { user } = panel({ applyAccept: false });
    const text = new File(["Morgan"], "notes.txt", { type: "text/plain" });
    await user.upload(screen.getByTestId("upload-cv-file"), text);
    expect(await screen.findByText(NOT_A_CV)).toBeVisible();
    expect(files.uploads).toEqual([]);
  });

  it("asks for the consent, then converts the file once its text is read", async () => {
    const api = fakeDocuments();
    const files = fakeFiles();
    filesAre(files, "done");
    const made = convertsInto(api, {});
    const { user } = panel();
    await user.upload(screen.getByTestId("upload-cv-file"), PDF);

    const dialog = await screen.findByRole("dialog");
    expect(conversions(api)).toEqual([]); // nothing is sent before the agreement
    await user.click(within(dialog).getByRole("checkbox"));
    await user.click(within(dialog).getByRole("button", { name: /^Agree/ }));

    await waitFor(() => {
      expect(conversions(api)).toHaveLength(1);
    });
    expect(conversions(api)[0]?.body).toEqual({
      from: "upload",
      ingestion_document_id: files.items[0]?.id,
    });
    const result = await screen.findByTestId("upload-result");
    expect(result).toHaveTextContent("Morgan CV is ready to edit.");
    expect(
      within(result).getByRole("link", { name: "Open it" }),
    ).toHaveAttribute("href", `/somewhere/${made.id}`);
    expect(result.textContent).not.toMatch(SEPARATOR);

    await user.click(
      within(result).getByRole("button", { name: "Make this my default CV" }),
    );
    await waitFor(() => {
      expect(
        api.requests.some(
          (one) =>
            one.method === "PATCH" &&
            one.path === `/api/documents/base-cvs/${made.id}`,
        ),
      ).toBe(true);
    });
  });

  it("offers the conversion again when the consent is declined, and sends nothing", async () => {
    const api = fakeDocuments();
    const files = fakeFiles();
    filesAre(files, "done");
    const { user } = panel();
    await user.upload(screen.getByTestId("upload-cv-file"), PDF);
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Not now" }));
    expect(await screen.findByTestId("upload-declined")).toHaveTextContent(
      "Confirm what is sent to convert Morgan CV.pdf.",
    );
    expect(conversions(api)).toEqual([]);
    await user.click(
      screen.getByRole("button", { name: "Convert it into my CV" }),
    );
    expect(await screen.findByRole("dialog")).toBeVisible();
  });

  it("says what the conversion waits for", async () => {
    const api = fakeDocuments({ consent: { accepted: true } });
    const files = fakeFiles();
    filesAre(files, "done");
    convertsInto(api, {
      generation: {
        id: "g-up",
        status: "waiting",
        wait_reason: "credential",
        error: null,
      },
    });
    const { user } = panel();
    await user.upload(screen.getByTestId("upload-cv-file"), PDF);
    expect(await screen.findByTestId("upload-waiting")).toHaveTextContent(
      "Waiting for a model credential. Add one in your account.",
    );
    expect(screen.queryByTestId("upload-result")).toBeNull();
  });

  it("shows ingestion's own reason when the file could not be read", async () => {
    const api = fakeDocuments({ consent: { accepted: true } });
    const files = fakeFiles();
    filesAre(files, "failed", {
      failed_reason: "There is no text in this file.",
    });
    const { user } = panel();
    await user.upload(screen.getByTestId("upload-cv-file"), PDF);
    expect(
      await screen.findByText("There is no text in this file."),
    ).toBeVisible();
    expect(conversions(api)).toEqual([]);
  });

  it("asks to keep a file with a secret warning before converting it", async () => {
    const api = fakeDocuments({ consent: { accepted: true } });
    const files = fakeFiles();
    filesAre(files, "done", { secret_warning: true });
    const { user } = panel();
    await user.upload(screen.getByTestId("upload-cv-file"), PDF);
    expect(
      await screen.findByText("This file may hold a password or a key"),
    ).toBeVisible();
    expect(conversions(api)).toEqual([]);
  });

  it("shows the api's sentence when the file cannot be converted", async () => {
    fakeDocuments({ consent: { accepted: true } });
    const files = fakeFiles();
    filesAre(files, "done");
    server.use(
      http.post("*/api/documents/base-cvs", () =>
        HttpResponse.json(
          { detail: "Upload your CV as a PDF or a Word document (DOCX)." },
          { status: 422 },
        ),
      ),
    );
    const { user } = panel();
    await user.upload(screen.getByTestId("upload-cv-file"), PDF);
    expect(
      await screen.findByText(
        "Upload your CV as a PDF or a Word document (DOCX).",
      ),
    ).toBeVisible();
  });

  it("starts the run from one button with its token range, then opens the review by itself", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const info = vi.spyOn(toast, "info");
    const api = fakeDocuments({ consent: { accepted: true } });
    const files = fakeFiles();
    filesAre(files, "done");
    convertsInto(api, { is_default: true });
    const runs = fakeRuns({ consent: { ...RUN_CONSENT, accepted: true } });
    const { user } = panel({
      advanceTimers: (ms) => vi.advanceTimersByTimeAsync(ms),
    });
    await user.upload(screen.getByTestId("upload-cv-file"), PDF);
    const result = await screen.findByTestId("upload-result");
    expect(result).toHaveTextContent("It is your default CV.");
    expect(
      within(result).queryByRole("button", { name: "Make this my default CV" }),
    ).toBeNull();
    expect(
      within(result).getByRole("link", { name: "Fill my profile page" }),
    ).toHaveAttribute("href", FILL_REVIEW);

    const start = await within(result).findByRole("button", {
      name: "Complete my profile from this CV (about 61,234 to 176,220 tokens)",
    });
    expect(runs.sent("POST", "/api/ingestion/runs/estimate")[0]?.body).toEqual({
      document_ids: [files.items[0]?.id],
      reread_document_ids: [],
    });
    // The figures are read, nothing runs before the click.
    expect(runs.sent("POST", "/api/ingestion/runs")).toEqual([]);
    expect(screen.getByTestId("where")).toHaveTextContent("/");

    await user.click(start);
    await waitFor(() => {
      expect(runs.sent("POST", "/api/ingestion/runs")).toHaveLength(1);
    });
    expect(
      await within(result).findByText(READING_FOR_PROFILE),
    ).toBeInTheDocument();
    // The run ends: the review opens by itself, and the person is told.
    runs.runs = runs.runs.map((run) => ({ ...run, status: "done" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RUN_POLL_MS);
    });
    await waitFor(() => {
      expect(screen.getByTestId("where")).toHaveTextContent(FILL_REVIEW);
    });
    expect(info).toHaveBeenCalledWith(REVIEW_OPENED);
  });

  it("asks both consents in one dialog, and records each with its own version", async () => {
    const api = fakeDocuments({ consent: { version: 4 } });
    const files = fakeFiles();
    filesAre(files, "done");
    convertsInto(api, {});
    const runs = fakeRuns({ consent: { ...RUN_CONSENT, version: 2 } });
    const { user } = panel();
    await user.upload(screen.getByTestId("upload-cv-file"), PDF);
    const dialog = await screen.findByRole("dialog");
    expect(
      await within(dialog).findByTestId("run-consent-text"),
    ).toHaveTextContent(RUN_CONSENT.text);
    const boxes = within(dialog).getAllByRole("checkbox");
    expect(boxes).toHaveLength(2);
    expect(
      within(dialog).getAllByRole("button", { name: /^Agree/ }),
    ).toHaveLength(1);
    expect(dialog.textContent).not.toMatch(SEPARATOR);
    for (const box of boxes) await user.click(box);
    await user.click(within(dialog).getByRole("button", { name: /^Agree/ }));
    await waitFor(() => {
      expect(runs.sent("POST", "/api/ingestion/consent")).toEqual([
        {
          method: "POST",
          path: "/api/ingestion/consent",
          body: { version: 2 },
        },
      ]);
    });
    expect(consents(api)).toEqual([{ version: 4 }]);
    await waitFor(() => {
      expect(conversions(api)).toHaveLength(1);
    });
    // Both given: the start button needs no other box.
    const result = await screen.findByTestId("upload-result");
    expect(
      await within(result).findByRole("button", {
        name: /^Complete my profile from this CV \(about/,
      }),
    ).toBeEnabled();
    expect(within(result).queryByRole("checkbox")).toBeNull();
  });

  it("records only the documents consent when the run's box stays empty, and asks it at the start", async () => {
    const api = fakeDocuments();
    const files = fakeFiles();
    filesAre(files, "done");
    convertsInto(api, {});
    const runs = fakeRuns();
    const { user } = panel();
    await user.upload(screen.getByTestId("upload-cv-file"), PDF);
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByTestId("run-consent-text");
    await user.click(
      within(dialog).getByRole("checkbox", {
        name: "I have read this and I agree to send it",
      }),
    );
    await user.click(within(dialog).getByRole("button", { name: /^Agree/ }));
    await waitFor(() => {
      expect(conversions(api)).toHaveLength(1);
    });
    expect(consents(api)).toEqual([{ version: 1 }]);
    expect(runs.sent("POST", "/api/ingestion/consent")).toEqual([]);
    // Only the other one is asked now, beside the start button.
    const result = await screen.findByTestId("upload-result");
    const start = await within(result).findByRole("button", {
      name: /^Complete my profile from this CV \(about/,
    });
    expect(start).toBeDisabled();
    await user.click(
      within(result).getByRole("checkbox", { name: /I have read this/ }),
    );
    expect(start).toBeEnabled();
  });
});

describe("the CV tab", () => {
  it("mounts the upload panel under the list", async () => {
    fakeSession(member);
    fakeProfile();
    fakeDocuments({ cvs: [fakeCv()] });
    renderApp("/profile/cv");
    const found = await screen.findByTestId("upload-panel");
    // The section around the panel names it, and the panel is a region of that name.
    expect(
      screen.getByRole("heading", { level: 2, name: "Upload your CV" }),
    ).toBeVisible();
    expect(found).toHaveAccessibleName("Upload your CV");
    expect(found.textContent).not.toMatch(SEPARATOR);
  });
});
