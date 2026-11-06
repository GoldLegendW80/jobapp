import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { fakeApplication } from "@/test/apply";
import { renderWithQuery } from "@/test/render";
import { server } from "@/test/server";
import {
  AgentFields,
  ChosenAnswers,
} from "@/components/shared/apply/agent-fields";
import { byStep } from "@/features/apply/helpers/agent-fields-state";
import type { Filled } from "@/features/apply/api/apply-api";

const FILLED: Filled = {
  fields: [
    {
      step: "Your details",
      label: "Full name",
      kind: "text",
      value: "Alex Example",
    },
    { step: "Your details", label: "Resume", kind: "file", value: "cv.pdf" },
    {
      step: "Your details",
      label: "Notice period *",
      kind: "text",
      value: "One month",
      chosen: "The profile names no notice period.",
    },
    {
      step: "Questions",
      label: "Why this company?",
      kind: "text",
      value: "Your robots.",
    },
  ],
  submit: "Submit application",
  fields_hash: "ab".repeat(32),
};

describe("AgentFields (A20)", () => {
  it("groups the fields by step in the order they were filled", () => {
    expect(byStep(FILLED.fields).map(([step, f]) => [step, f.length])).toEqual([
      ["Your details", 3],
      ["Questions", 1],
    ]);
  });

  it("keeps two fields of one label as two rows (round 1 of the review)", () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    renderWithQuery(
      <AgentFields
        application={fakeApplication("ready", { mode: "review" })}
        filled={{
          ...FILLED,
          fields: [
            {
              step: "Work",
              label: "Company",
              kind: "text",
              value: "Example Robotics",
            },
            {
              step: "Work",
              label: "Company",
              kind: "text",
              value: "Example Labs",
            },
          ],
        }}
        onChanged={() => {}}
      />,
    );
    expect(screen.getByText("Example Robotics")).toBeInTheDocument();
    expect(screen.getByText("Example Labs")).toBeInTheDocument();
    expect(screen.getAllByText("Company")).toHaveLength(2);
    // No duplicate key warning from React.
    expect(
      errors.mock.calls.some((call) => String(call[0]).includes("same key")),
    ).toBe(false);
    errors.mockRestore();
  });

  it("lists every filled field and confirms with the hash it showed, at phone width", async () => {
    window.innerWidth = 375;
    window.dispatchEvent(new Event("resize"));
    let sent: unknown = null;
    server.use(
      http.post("*/api/applications/:id/confirm", async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json(fakeApplication("approved"));
      }),
    );
    const onChanged = vi.fn();
    renderWithQuery(
      <AgentFields
        application={fakeApplication("ready", { mode: "review" })}
        filled={FILLED}
        onChanged={onChanged}
      />,
    );
    const list = screen.getByRole("region", {
      name: "What the assistant filled",
    });
    for (const value of ["Alex Example", "cv.pdf", "One month", "Your robots."])
      expect(within(list).getByText(value)).toBeInTheDocument();
    expect(
      within(list).getByText("Chosen by the assistant"),
    ).toBeInTheDocument();
    expect(within(list).getByText("Questions")).toBeInTheDocument();
    expect(screen.queryByText(/question for you/i)).toBeNull();
    expect(screen.queryByText(/left for you/i)).toBeNull();
    const confirm = screen.getByRole("button", { name: "Confirm and send" });
    expect(screen.getAllByRole("button")).toHaveLength(2); // Confirm and send, Cancel
    await userEvent.click(confirm);
    await waitFor(() => {
      expect(onChanged).toHaveBeenCalled();
    });
    expect(sent).toEqual({ fields_hash: "ab".repeat(32) });
    for (const text of screen.getAllByText(/./).map((n) => n.textContent))
      expect(text).not.toMatch(/—|–| - /);
  });

  it("shows the refusal when the held session is gone", async () => {
    server.use(
      http.post("*/api/applications/:id/confirm", () =>
        HttpResponse.json(
          {
            detail:
              "The browser session ended before you confirmed, so nothing was sent.",
            code: "session_lost",
          },
          { status: 409 },
        ),
      ),
    );
    renderWithQuery(
      <AgentFields
        application={fakeApplication("ready", { mode: "review" })}
        filled={FILLED}
        onChanged={vi.fn()}
      />,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Confirm and send" }),
    );
    expect(await screen.findByText(/nothing was sent/)).toBeInTheDocument();
  });

  it("is read only once nothing waits for the Confirm", () => {
    renderWithQuery(
      <AgentFields
        application={fakeApplication("submitted", { mode: "review" })}
        filled={{ ...FILLED, fields_hash: null }}
        onChanged={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText("Alex Example")).toBeInTheDocument();
  });

  it("lists the answers One click Apply chose", () => {
    renderWithQuery(
      <ChosenAnswers
        chosen={[
          {
            label: "Gender",
            value: "Prefer not to say",
            why: "Not in the profile.",
          },
        ]}
      />,
    );
    expect(screen.getByText("Prefer not to say")).toBeInTheDocument();
  });
});
