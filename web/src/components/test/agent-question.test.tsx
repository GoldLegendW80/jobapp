import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { fakeApplication } from "@/test/apply";
import { makeDetail } from "@/test/jobs";
import { renderWithQuery } from "@/test/render";
import { server } from "@/test/server";
import {
  AFTER_SEND,
  AgentQuestion,
} from "@/components/shared/apply/agent-question";
import { valueText } from "@/features/apply/helpers/apply-state";
import { NeedsPerson } from "@/components/shared/apply/needs-person";
import { needOf } from "@/features/apply/helpers/needs-person-state";

const DASHES = /[–—]| - /;
const FACT = {
  sentence:
    'The assistant needs your answer for "Notice period *" on jobs.example.org.',
  label: "Notice period *",
  host: "jobs.example.org",
  kind: "fact" as const,
};

function asked() {
  return {
    ...fakeApplication("needs_person"),
    mode: "direct",
    needs: "agent_question",
  };
}

describe("One click Apply's question (A19)", () => {
  it("shows the server's sentence and sends the answer with Remember", async () => {
    const sent: unknown[] = [];
    const application = asked();
    server.use(
      http.post("*/api/applications/:id/question", async ({ request }) => {
        sent.push(await request.json());
        return HttpResponse.json({
          ...application,
          state: "queued",
          needs: null,
        });
      }),
    );
    renderWithQuery(
      <NeedsPerson
        application={application}
        job={makeDetail()}
        question={FACT}
      />,
    );
    expect(screen.getByText(FACT.sentence)).toBeInTheDocument();
    const send = screen.getByRole("button", { name: "Send the answer" });
    expect(send).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Your answer"), "Two months");
    await userEvent.click(screen.getByLabelText("Remember this answer"));
    await userEvent.click(send);
    await waitFor(() => {
      expect(sent).toEqual([{ answer: "Two months", remember: true }]);
    });
  });

  it("asks an action of the person on the site, with no text box", () => {
    renderWithQuery(
      <NeedsPerson
        application={asked()}
        job={makeDetail()}
        question={{ ...FACT, kind: "action", sentence: "Do it there." }}
      />,
    );
    expect(screen.queryByLabelText("Your answer")).toBeNull();
    expect(
      screen.getByRole("button", { name: "I did it, go on" }),
    ).toBeEnabled();
  });

  it.each([
    ["Verify you are human", "Open the live view to solve the check"],
    ["Code de vérification", "Open the live view to enter the code"],
    ["Password", "Open the live view to sign in"],
    ["Upload a copy of your ID", "Open the live view"],
  ])(
    "names the live view after the step, %s, and keeps I did it, go on the one primary",
    (label, open) => {
      renderWithQuery(
        <AgentQuestion
          application={asked()}
          question={{
            ...FACT,
            kind: "action",
            label,
            sentence: "Do it there.",
          }}
        />,
      );
      expect(screen.getByRole("button", { name: open })).toHaveAttribute(
        "data-variant",
        "outline",
      );
      expect(
        screen.getByRole("button", { name: "I did it, go on" }),
      ).toHaveAttribute("data-variant", "default");
    },
  );

  it("after a possible send, warns first and makes the answer a Try again", async () => {
    const sent: unknown[] = [];
    const application = {
      ...fakeApplication("submit_unknown"),
      mode: "direct",
    };
    server.use(
      http.post("*/api/applications/:id/question", async ({ request }) => {
        sent.push(await request.json());
        return HttpResponse.json({ ...application, state: "queued" });
      }),
    );
    renderWithQuery(
      <AgentQuestion
        application={application}
        question={{ ...FACT, after_send: true }}
      />,
    );
    expect(screen.getByText(FACT.sentence)).toBeInTheDocument();
    expect(screen.getByText(AFTER_SEND)).toBeInTheDocument();
    expect(AFTER_SEND).not.toMatch(DASHES);
    expect(screen.getByLabelText("Remember this answer")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Your answer"), "Two months");
    await userEvent.click(
      screen.getByRole("button", { name: "Try again with this answer" }),
    );
    await waitFor(() => {
      expect(sent).toEqual([{ answer: "Two months", remember: false }]);
    });
  });

  it("after a possible send, an action is done on the site, then tried again", () => {
    renderWithQuery(
      <AgentQuestion
        application={{ ...fakeApplication("submit_unknown"), mode: "direct" }}
        question={{ ...FACT, kind: "action", after_send: true }}
      />,
    );
    expect(screen.queryByLabelText("Your answer")).toBeNull();
    expect(
      screen.getByRole("button", { name: "I did it, try again" }),
    ).toBeEnabled();
  });

  it("shows the run report as text, not as raw JSON", () => {
    const report = {
      summary: "Filled both steps and sent it.",
      confirmation_url: "https://jobs.example.org/thanks",
    };
    expect(valueText(report)).toBe(
      "Filled both steps and sent it. Confirmation page: https://jobs.example.org/thanks",
    );
    expect(valueText({ summary: "It failed.", confirmation_url: "" })).toBe(
      "It failed.",
    );
    expect(valueText({ other: 1 })).toBe('{"other":1}');
  });

  it("names the need in plain words, with no dash", () => {
    const need = needOf(asked());
    expect(need.title).toBe("The assistant has a question for you");
    expect(`${need.title} ${need.what}`).not.toMatch(DASHES);
  });
});
