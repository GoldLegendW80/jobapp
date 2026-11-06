import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it } from "vitest";

import { createQueryClient } from "@/lib/query";
import { fakeCv, fakeDocumentOf } from "@/test/documents";
import { phoneScreen } from "@/test/media";
import { server } from "@/test/server";

import type { Panel, PanelOut } from "./documents-api";
import { PanelLine, PanelPanel } from "./panel-panel";
import { panelSentence } from "./panel-text";

const DOC = "doc-panel-1";
const ROLES = [
  ["screen", "The automated screen", "REJECT"],
  ["manager", "The hiring manager", "INTERVIEW"],
  ["reader_manager", "Reader: the hiring manager", "HIRE"],
  ["reader_rival", "Reader: another applicant", "MAYBE"],
  ["reader_ceo", "Reader: the CEO", "HIRE"],
  ["reader_recruiter", "Reader: the recruiter", "HIRE"],
  ["reader_teammate", "Reader: the teammate", "HIRE"],
] as const;

function fakePanel(overrides: Partial<Panel> = {}): Panel {
  return {
    id: "panel-1",
    status: "done",
    kind: "cv",
    reviewed_version: 1,
    rebuilt_version: 2,
    reviews: ROLES.map(([role, label, verdict]) => ({
      role,
      label,
      verdict,
      missing: null,
      line: "Platform Engineer, cloud and backend",
      why: "Generic.",
    })),
    mistakes: [
      {
        number: 1,
        quote: "Platform Engineer, cloud and backend",
        why: "Says nothing of payments.",
        flagged_by: ["screen", "reader_rival"],
        fix: "Name the payments platform.",
      },
    ],
    questions: [
      { question: "How many services moved?", section: "experience" },
    ],
    screen_before: "REJECT",
    screen_after: "PASS",
    readers_hire: 4,
    readers_answered: 5,
    strong: false,
    closest: null,
    failed: [],
    created_at: "2026-11-06T09:00:00Z",
    finished_at: "2026-11-06T09:05:00Z",
    ...overrides,
  };
}

function serve(panel: Panel | null) {
  const body: PanelOut = { panel, input_tokens: 1200, output_tokens: 300 };
  server.use(
    http.get(`*/api/documents/${DOC}/panel`, () => HttpResponse.json(body)),
  );
}

function show(ui: React.ReactElement) {
  const router = createMemoryRouter([{ path: "/", element: ui }], {
    initialEntries: ["/"],
  });
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

const document = fakeDocumentOf(fakeCv({ id: DOC }));
const DASHES = /—|–| - /;

afterEach(() => {
  window.document.documentElement.classList.remove("dark");
});

describe("the card's line", () => {
  it("says the screen before and after and how many readers would hire", async () => {
    serve(fakePanel());
    show(<PanelLine documentId={DOC} />);
    expect(await screen.findByTestId("panel-line")).toHaveTextContent(
      "Hiring panel: screen PASS, was REJECT; 4 of 5 readers would hire",
    );
  });

  it("says nothing when no panel ran, and says a running panel reads it", async () => {
    expect(panelSentence(fakePanel({ status: "running" }))).toBe(
      "Hiring panel: seven reviewers are reading it",
    );
    expect(panelSentence(fakePanel({ screen_after: null }))).toBe(
      "Hiring panel: screen REJECT; 4 of 5 readers would hire",
    );
    serve(null);
    const { container } = show(<PanelLine documentId={DOC} />);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(container.textContent).toBe("");
  });
});

describe("the Panel tab", () => {
  it("lists every reviewer, the numbered mistakes, the screens and the questions, at phone width", async () => {
    phoneScreen();
    serve(fakePanel());
    show(<PanelPanel document={document} generation={null} />);
    const view = await screen.findByTestId("panel-view");
    const table = within(view).getByRole("table");
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(8);
    expect(rows[1]).toHaveTextContent("The automated screen");
    expect(rows[1]).toHaveTextContent("REJECT");
    // On a phone the line that hurt most sits under the reviewer, in the first column.
    expect(within(rows[1]!).getAllByRole("cell")[0]).toHaveTextContent(
      "“Platform Engineer, cloud and backend”",
    );
    const mistakes = within(view).getByRole("list", {
      name: "What was wrong, and how it was fixed",
    });
    expect(mistakes).toHaveTextContent(
      "1. “Platform Engineer, cloud and backend”",
    );
    expect(mistakes).toHaveTextContent("Fixed: Name the payments platform.");
    expect(within(view).getByTestId("panel-screens")).toHaveTextContent(
      "The automated screen: REJECT before, PASS after the rewrite. Version 2 is the rewrite; version 1 stays in the history.",
    );
    const link = within(view).getByRole("link", {
      name: "Add it in Experience",
    });
    expect(link).toHaveAttribute("href", "/profile?tab=experience");
    expect(view.textContent).not.toMatch(DASHES);
  });

  it("says a strong document is strong and names the reviewer that came closest", async () => {
    serve(
      fakePanel({
        mistakes: [],
        strong: true,
        rebuilt_version: null,
        closest: {
          role: "screen",
          label: "The automated screen",
          why: "two terms of the posting were missing, the rest held.",
        },
      }),
    );
    show(<PanelPanel document={document} generation={null} />);
    expect(await screen.findByTestId("panel-strong")).toHaveTextContent(
      "The reviewers found nothing to fix. The automated screen came closest to rejecting it: two terms of the posting were missing, the rest held.",
    );
  });

  it("says what failed and that the first version stays, in light and dark", async () => {
    for (const dark of [false, true]) {
      window.document.documentElement.classList.toggle("dark", dark);
      serve(
        fakePanel({
          status: "failed",
          failed: ["merge"],
          rebuilt_version: null,
        }),
      );
      const { unmount, container } = show(
        <PanelPanel document={document} generation={null} />,
      );
      expect(await screen.findByTestId("panel-failed")).toHaveTextContent(
        "The first version stays",
      );
      // Theme tokens only: no colour fixed for one theme.
      expect(container.innerHTML).not.toMatch(
        /\b(bg-white|text-black|bg-black|text-white)\b/,
      );
      expect(container.textContent).not.toMatch(DASHES);
      unmount();
    }
  });

  it("says a posting sent during the panel keeps the version sent", async () => {
    serve(
      fakePanel({
        status: "failed",
        failed: ["posting_applied"],
        rebuilt_version: null,
        screen_after: null,
      }),
    );
    show(<PanelPanel document={document} generation={null} />);
    const failed = await screen.findByTestId("panel-failed");
    expect(failed).toHaveTextContent(
      "No rewrite: the posting was sent meanwhile, so the version sent stays",
    );
    expect(failed.textContent).not.toMatch(DASHES);
  });

  it("never says a mistake was fixed when the rewrite did not finish", async () => {
    serve(
      fakePanel({
        status: "failed",
        failed: ["rebuild"],
        rebuilt_version: null,
        screen_after: null,
      }),
    );
    show(<PanelPanel document={document} generation={null} />);
    const mistakes = await screen.findByRole("list", {
      name: "What was wrong, and how it was fixed",
    });
    expect(mistakes).toHaveTextContent("To fix: Name the payments platform.");
    expect(mistakes).not.toHaveTextContent("Fixed:");
  });

  it("says no panel has read it yet", async () => {
    serve(null);
    show(<PanelPanel document={document} generation={null} />);
    expect(await screen.findByTestId("panel-none")).toHaveTextContent(
      "No hiring panel has read this document yet.",
    );
  });
});
