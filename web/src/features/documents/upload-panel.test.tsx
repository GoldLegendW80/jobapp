import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

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

import type { BaseCv } from "./documents-api";
import type { DocumentStatus } from "@/features/ingestion/documents-api";
import { FILL_REVIEW, NOT_A_CV, UploadPanel } from "./upload-panel";

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
    </MemoryRouter>,
  );
  return { user, ...view };
}

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
    expect(conversions(api)).toEqual([]); // the text is not read yet
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

  it("opens ingestion's run dialog on the file, and nothing runs until Start", async () => {
    const api = fakeDocuments({ consent: { accepted: true } });
    const files = fakeFiles();
    filesAre(files, "done");
    convertsInto(api, { is_default: true });
    const runs = fakeRuns();
    const { user } = panel();
    await user.upload(screen.getByTestId("upload-cv-file"), PDF);
    const result = await screen.findByTestId("upload-result");
    expect(result).toHaveTextContent("It is your default CV.");
    expect(
      within(result).queryByRole("button", { name: "Make this my default CV" }),
    ).toBeNull();
    expect(
      within(result).getByRole("link", { name: "Fill my profile page" }),
    ).toHaveAttribute("href", FILL_REVIEW);

    await user.click(
      within(result).getByRole("button", {
        name: "Complete my profile from this CV",
      }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Before the run starts",
    });
    await waitFor(() => {
      expect(runs.sent("POST", "/api/ingestion/runs/estimate")).toHaveLength(1);
    });
    expect(runs.sent("POST", "/api/ingestion/runs/estimate")[0]?.body).toEqual({
      document_ids: [files.items[0]?.id],
      reread_document_ids: [],
    });
    expect(runs.sent("POST", "/api/ingestion/runs")).toEqual([]);
    expect(
      within(dialog).getByRole("button", { name: /Start the run/ }),
    ).toBeVisible();
  });
});

describe("the CV tab", () => {
  it("mounts the upload panel under the list", async () => {
    fakeSession(member);
    fakeProfile();
    fakeDocuments({ cvs: [fakeCv()] });
    renderApp("/profile/cv");
    const found = await screen.findByTestId("upload-panel");
    expect(
      within(found).getByRole("heading", { name: "Upload your CV" }),
    ).toBeVisible();
    expect(found.textContent).not.toMatch(SEPARATOR);
  });
});
