import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";

import { fakeAnswer } from "@/test/apply";
import { server } from "@/test/server";

import { getApproval, type Approval } from "./apply-api";
import { ApproveDialog } from "./approve-dialog";
import { EmailApplication } from "./email-application";

function approval(): Approval {
  return {
    state: "ready",
    dry_run: false,
    answers_hash: "a".repeat(64),
    documents: { cv: { number: 2 }, letter: { number: 3 } },
    screenshots: [],
    answers: [
      fakeAnswer("email_to", "Email recipient", "e2e-jobs@example.org"),
      fakeAnswer("email_subject", "Email subject", "Robotics Engineer", {
        max_chars: 998,
      }),
      fakeAnswer(
        "email_body",
        "Email message",
        "Please find my application attached.",
      ),
    ],
    email: { written_by: "agent", issues: [] },
  };
}

function props(current = approval()) {
  return {
    open: true,
    onOpenChange: vi.fn(),
    company: "Example Robotics",
    role: "Robotics Engineer",
    approval: current,
    pending: false,
    onConfirm: vi.fn(),
  };
}

describe("email application approval", () => {
  it("accepts the exact email subject limit through the real approval API client", async () => {
    server.use(
      http.get("/api/applications/application/approval", () =>
        HttpResponse.json(approval()),
      ),
    );
    const current = await getApproval("application");
    expect(
      current.answers.find((answer) => answer.field_key === "email_subject")
        ?.max_chars,
    ).toBe(998);
  });

  it("loads the exact email and asks Sent reconciliation through the normal API client", async () => {
    const user = userEvent.setup();
    let checked = 0;
    server.use(
      http.get("/api/applications/application/email", () =>
        HttpResponse.json({
          id: "draft",
          status: "send_unknown",
          to: ["e2e-jobs@example.org"],
          subject: "Robotics Engineer",
          body: "Please find my application attached.",
          send_id: "send",
          message: null,
          issues: [],
        }),
      ),
      http.post("/api/mail/sends/send/reconcile", () => {
        checked += 1;
        return HttpResponse.json({ resolved: 0 });
      }),
    );
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <EmailApplication applicationId="application" />
      </QueryClientProvider>,
    );
    expect(
      await screen.findByText("Please find my application attached."),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Check Sent" }));
    await waitFor(() => {
      expect(checked).toBe(1);
    });
  });
  it("shows the exact message and document versions and requires the new recipient confirmation", async () => {
    const user = userEvent.setup();
    const values = props();
    render(<ApproveDialog {...values} />);
    expect(screen.getByText("To: e2e-jobs@example.org")).toBeInTheDocument();
    expect(
      screen.getByText("Please find my application attached."),
    ).toBeInTheDocument();
    const send = screen.getByRole("button", { name: "Approve and submit" });
    expect(send).toBeDisabled();
    await user.click(
      screen.getByRole("checkbox", {
        name: /I confirm this new email recipient/,
      }),
    );
    await user.click(send);
    expect(values.onConfirm).toHaveBeenCalledOnce();
  });

  it("requires a fresh recipient confirmation after the approved hash changes", async () => {
    const user = userEvent.setup();
    const current = approval();
    const values = props(current);
    const view = render(<ApproveDialog {...values} />);
    await user.click(
      screen.getByRole("checkbox", {
        name: /I confirm this new email recipient/,
      }),
    );
    view.rerender(
      <ApproveDialog
        {...values}
        approval={{ ...current, answers_hash: "b".repeat(64) }}
      />,
    );
    expect(
      screen.getByRole("button", { name: "Approve and submit" }),
    ).toBeDisabled();
  });

  it("lets the person's text acknowledge its own fact-check issue", async () => {
    const user = userEvent.setup();
    const current = approval();
    current.email = {
      written_by: "user",
      issues: [
        { id: "number:0:3", code: "number_not_in_sources", start: 0, end: 3 },
      ],
    };
    const values = props(current);
    render(<ApproveDialog {...values} />);
    await user.click(
      screen.getByRole("checkbox", {
        name: /I confirm this new email recipient/,
      }),
    );
    expect(
      screen.getByRole("button", { name: "Approve and submit" }),
    ).toBeDisabled();
    await user.click(
      screen.getByRole("checkbox", { name: /I checked this claim/ }),
    );
    await user.click(
      screen.getByRole("button", { name: "Approve and submit" }),
    );
    expect(values.onConfirm).toHaveBeenCalledWith(["number:0:3"]);
  });

  it("requires an edit for the agent's unsupported claim", async () => {
    const user = userEvent.setup();
    const current = approval();
    current.email = {
      written_by: "agent",
      issues: [
        { id: "number:0:3", code: "number_not_in_sources", start: 0, end: 3 },
      ],
    };
    render(<ApproveDialog {...props(current)} />);
    await user.click(
      screen.getByRole("checkbox", {
        name: /I confirm this new email recipient/,
      }),
    );
    expect(
      screen.getByRole("checkbox", { name: /I checked this claim/ }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Approve and submit" }),
    ).toBeDisabled();
  });
});
