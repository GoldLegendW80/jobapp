import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { fakeAnswer } from "@/test/apply";

import type { Approval } from "./apply-api";
import { ApproveDialog } from "./approve-dialog";

function approval(): Approval {
  return {
    state: "ready",
    dry_run: false,
    answers_hash: "a".repeat(64),
    documents: { cv: { number: 2 }, letter: { number: 3 } },
    screenshots: [],
    answers: [
      fakeAnswer("email_to", "Email recipient", "e2e-jobs@example.org"),
      fakeAnswer("email_subject", "Email subject", "Robotics Engineer"),
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
