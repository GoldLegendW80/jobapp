import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { fakeSession, member } from "@/test/auth";
import { fakeCv, fakeDocuments } from "@/test/documents";
import { fakeProfile } from "@/test/profile";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";
import { TEMPLATES } from "@/test/templates";
import type { BaseCv, Generation } from "./documents-api";

const capture = (
  status: Generation["status"],
  extra: Partial<Generation> = {},
): Generation => ({
  id: "capture",
  status,
  wait_reason: null,
  error: null,
  ...extra,
});
async function show(
  design: Generation | null,
  extra: Partial<BaseCv> = {},
  path = "/profile/cv",
) {
  fakeSession(member);
  fakeProfile();
  const api = fakeDocuments({
    cvs: [fakeCv({ source: "uploaded", design_generation: design, ...extra })],
  });
  server.use(
    http.get("*/api/document-templates", () =>
      HttpResponse.json({ templates: TEMPLATES }),
    ),
  );
  const user = userEvent.setup();
  const app = renderApp(path);
  await screen.findByText(
    extra.source === "built"
      ? "Built in the app"
      : extra.source === "profile"
        ? "From your profile"
        : "Uploaded",
  );
  return { api, user, ...app };
}

describe("uploaded CV capture controls", () => {
  it.each([
    ["queued", "Waiting to copy your CV's design."],
    ["running", "Copying your CV's design."],
    [
      "failed",
      "Only the design copy failed. Your current template and content are still available.",
    ],
    [
      "cancelled",
      "The design copy stopped. Your current template and content are still available.",
    ],
    ["done", "Your CV's design has been copied."],
  ] as const)(
    "announces %s independently of finished content",
    async (status, text) => {
      await show(capture(status), { template_slug: "upload-existing" });
      expect(screen.getByText(text).closest('[role="status"]')).toHaveAttribute(
        "aria-live",
        "polite",
      );
      expect(
        screen.getByRole("link", { name: "Choose a template" }),
      ).toHaveAttribute("href", "/documents/templates?document=cv-1");
      expect(
        screen.getByRole("button", { name: "Try the design again" }),
      ).toHaveAttribute(
        "aria-disabled",
        String(status === "queued" || status === "running"),
      );
      expect(
        screen.queryByText(/Classic was kept|last attempt failed/i),
      ).toBeNull();
      if (status === "queued" || status === "running")
        expect(
          screen.getByText(
            "Other changes may wait while the design is copied.",
          ),
        ).toBeVisible();
    },
  );

  it.each([
    ["consent", "Confirm what is sent", "/profile/cv"],
    ["credential", "Add a model credential", "/account"],
  ] as const)(
    "offers the existing %s destination",
    async (reason, label, href) => {
      const { user } = await show(capture("waiting", { wait_reason: reason }));
      expect(screen.getByText(/To copy your CV's design/)).toBeVisible();
      if (reason === "consent") {
        await user.click(screen.getByRole("button", { name: label }));
        expect(
          await screen.findByRole("dialog", {
            name: "What is sent to resume your documents",
          }),
        ).toBeVisible();
        await user.click(screen.getByRole("button", { name: "Not now" }));
      } else {
        expect(screen.getByRole("link", { name: label })).toHaveAttribute(
          "href",
          href,
        );
      }
      expect(
        screen.getByRole("button", { name: "Try the design again" }),
      ).toHaveAttribute("aria-disabled", "true");
    },
  );

  it("shows missing-original guidance without an internal error or filename", async () => {
    await show(capture("failed", { error: "binding_gone" }));
    expect(
      screen.getByText("Upload your original CV again to copy its design."),
    ).toBeVisible();
    expect(document.body.textContent).not.toMatch(/binding_gone/);
  });

  it("keeps legacy capture idle and blocks retry during content writing", async () => {
    await show(null, { generation: capture("running") });
    expect(
      screen.getByText("Your CV's design has not been copied yet."),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Try the design again" }),
    ).toHaveAttribute("aria-disabled", "true");
  });

  it.each(["profile", "built"] as const)(
    "adds no capture controls to %s CVs",
    async (source) => {
      await show(null, { source });
      expect(
        screen.queryByRole("button", { name: "Try the design again" }),
      ).toBeNull();
    },
  );

  it("sends one empty-body POST under keyboard and repeated activation, retaining focus", async () => {
    const { api, user } = await show(null);
    let release: (() => void) | undefined;
    let calls = 0;
    server.use(
      http.post("*/api/documents/cv-1/design/capture", async ({ request }) => {
        calls += 1;
        expect(await request.text()).toBe("");
        expect(request.headers.get("x-requested-with")).toBe("jobapp");
        await new Promise<void>((resolve) => {
          release = resolve;
        });
        api.cvs[0] = { ...api.cvs[0]!, design_generation: capture("queued") };
        return HttpResponse.json(capture("queued"), { status: 202 });
      }),
    );
    const retry = screen.getByRole("button", { name: "Try the design again" });
    retry.focus();
    await user.keyboard("{Enter}{Enter} ");
    await user.dblClick(retry);
    await waitFor(() => {
      expect(calls).toBe(1);
    });
    expect(retry).toHaveAttribute("aria-disabled", "true");
    expect(retry).toHaveFocus();
    release!();
    expect(
      await screen.findByText("Waiting to copy your CV's design."),
    ).toBeVisible();
    expect(retry).toHaveFocus();
    expect(calls).toBe(1);
  });

  it("refreshes a conflict with fixed copy associated with this CV", async () => {
    const { api, user } = await show(null);
    server.use(
      http.post("*/api/documents/cv-1/design/capture", () => {
        api.cvs[0] = { ...api.cvs[0]!, design_generation: capture("running") };
        return HttpResponse.json(
          { detail: "synthetic-private-context" },
          { status: 409 },
        );
      }),
    );
    await user.click(
      screen.getByRole("button", { name: "Try the design again" }),
    );
    expect(
      await screen.findByText(
        "Another change is in progress for this CV. Its latest state has been refreshed.",
      ),
    ).toBeVisible();
    expect(await screen.findByText("Copying your CV's design.")).toBeVisible();
    expect(
      within(screen.getByRole("list", { name: "Your CVs" })).getByText(
        /Another change/,
      ),
    ).toBeVisible();
    expect(document.body.textContent).not.toContain(
      "synthetic-private-context",
    );
  });

  it("shows a missing original returned by retry without retrying it automatically", async () => {
    const { api, user } = await show(null);
    let calls = 0;
    server.use(
      http.post("*/api/documents/cv-1/design/capture", () => {
        calls += 1;
        const gone = capture("failed", { error: "binding_gone" });
        api.cvs[0] = { ...api.cvs[0]!, design_generation: gone };
        return HttpResponse.json(gone, { status: 202 });
      }),
    );
    await user.click(
      screen.getByRole("button", { name: "Try the design again" }),
    );
    expect(
      await screen.findByText(
        "Upload your original CV again to copy its design.",
      ),
    ).toBeVisible();
    expect(calls).toBe(1);
  });

  it("keeps generic retry errors on the selected CV and hides arbitrary server details", async () => {
    const { api, user, client } = await show(null);
    api.cvs.push(
      fakeCv({
        id: "other",
        name: "Other CV",
        source: "uploaded",
        is_default: false,
      }),
    );
    await client.invalidateQueries({ queryKey: ["documents", "base-cvs"] });
    server.use(
      http.post("*/api/documents/cv-1/design/capture", () =>
        HttpResponse.json(
          { detail: "synthetic-private-error" },
          { status: 500 },
        ),
      ),
    );
    await screen.findByRole("link", { name: "Other CV" });
    const rows = within(
      screen.getByRole("list", { name: "Your CVs" }),
    ).getAllByRole("listitem");
    await user.click(
      within(rows[0]!).getByRole("button", { name: "Try the design again" }),
    );
    expect(
      await within(rows[0]!).findByText(
        "The design could not be started. Try again.",
      ),
    ).toBeVisible();
    expect(within(rows[1]!).queryByText(/could not be started/)).toBeNull();
    expect(document.body.textContent).not.toContain("synthetic-private-error");
  });

  it.each(["failed", "running"] as const)(
    "keeps opened content, editing and download available during capture %s",
    async (status) => {
      const { user } = await show(capture(status), {}, "/profile/cv/cv-1");
      expect(
        screen.getByText(
          status === "failed"
            ? /Only the design copy failed/
            : "Copying your CV's design.",
        ),
      ).toBeVisible();
      expect(
        await screen.findByRole("link", { name: "Download PDF" }),
      ).toBeVisible();
      await user.click(screen.getByRole("tab", { name: "Edit" }));
      expect(screen.getByRole("tab", { name: "Edit" })).toHaveAttribute(
        "aria-selected",
        "true",
      );
      expect(screen.queryByText("The last attempt failed")).toBeNull();
    },
  );
});
