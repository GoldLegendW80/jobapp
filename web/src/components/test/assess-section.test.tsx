import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";

import {
  ASSESSING,
  ASSESSMENT_RESULT,
  assessmentView,
  fakeAssessment,
  NEVER_ASSESSED,
} from "@/design-system/fixtures/posting-assessment";
import { admin, fakeSession } from "@/test/auth";
import { makeDetail } from "@/test/jobs";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

const JOB = "00000000-0000-4000-9000-000000000001";

function openPosting(view = assessmentView()) {
  fakeSession(admin);
  server.use(
    http.get("*/api/jobs/:id", () =>
      HttpResponse.json(makeDetail({ fit: 91 })),
    ),
  );
  const starts = fakeAssessment(view);
  renderApp(`/jobs/${JOB}`);
  return starts;
}

const section = () =>
  screen.findByRole("region", { name: "Assess this posting" });
const assessButton = () =>
  screen.getByRole("button", { name: /^(Assess|Assessing)$/ });

// The fold remembers its open state (AdvancedSection's pageKey): each test starts folded.
beforeEach(() => {
  window.localStorage.clear();
});

describe("job detail: assess this posting", () => {
  it("starts one run on the click and follows it", async () => {
    const starts = openPosting(NEVER_ASSESSED);
    const rail = await section();
    await waitFor(() => {
      expect(assessButton()).toBeEnabled();
    });
    expect(within(rail).queryByTestId("assessment-summary")).toBeNull();
    expect(screen.queryByRole("region", { name: "Assessment" })).toBeNull();
    await userEvent.click(assessButton());
    expect(await within(rail).findByRole("status")).toHaveTextContent(
      "Reading the posting and your profile.",
    );
    expect(assessButton()).toHaveTextContent("Assessing");
    expect(assessButton()).toBeDisabled();
    expect(starts).toEqual([JOB]);
  });

  it("shows the verdict beside the keyword fit, which it leaves as it is", async () => {
    openPosting();
    const rail = await section();
    const summary = await within(rail).findByTestId("assessment-summary");
    expect(summary).toHaveTextContent(ASSESSMENT_RESULT.verdict);
    expect(summary).toHaveTextContent("How sureMedium");
    // Fluent French (critical, partial) and Kafka (high, missing).
    expect(summary).toHaveTextContent("Gaps to prepare2");
    expect(
      within(summary).getByLabelText("Fit 91 out of 100"),
    ).toBeInTheDocument();
    expect(summary).toHaveTextContent("Assessed7 Nov 2026");
  });

  it("folds the whole assessment under the posting, gaps first with what to say", async () => {
    openPosting();
    const details = await screen.findByRole("region", { name: "Assessment" });
    expect(details).toHaveTextContent(ASSESSMENT_RESULT.verdict);
    await userEvent.click(
      within(details).getByRole("button", {
        name: "Requirements, culture, signals and the role",
      }),
    );
    const rows = within(details).getAllByTestId("requirement");
    expect(rows.map((row) => row.getAttribute("data-match"))).toEqual([
      "partial",
      "strong",
      "missing",
      "strong",
    ]);
    expect(rows[0]).toHaveTextContent("Fluent French");
    expect(rows[0]).toHaveTextContent(
      "Interview risk: The first call may happen in French.",
    );
    expect(rows[0]).toHaveTextContent("What to say: Say you work in French");
    expect(rows[3]).toHaveTextContent("Weight: inferred");
    expect(details).toHaveTextContent(
      "3 less important requirements are not listed.",
    );
    const criteria = within(details).getByRole("list", {
      name: "Your criteria",
    });
    expect(within(criteria).getAllByRole("listitem")).toHaveLength(3);
    expect(criteria).toHaveTextContent("Permanent contractNot said");
    expect(
      within(details).getByRole("list", { name: "Signals" }),
    ).toHaveTextContent("It names the team's size and what it owns.");
    const ask = within(details).getByRole("list", {
      name: "Ask before you commit",
    });
    expect(within(ask).getAllByRole("listitem")).toHaveLength(5);
  });

  it("says when the role check could not read the text, and the criteria are none", async () => {
    openPosting(
      assessmentView({
        assessment: {
          run_id: null,
          created_at: "2026-11-07T08:30:00Z",
          result: {
            ...ASSESSMENT_RESULT,
            culture: [],
            signals: [],
            more_requirements: 0,
            role_clarity: { read: false, points: [], questions: [] },
          },
        },
      }),
    );
    const details = await screen.findByRole("region", { name: "Assessment" });
    await userEvent.click(
      within(details).getByRole("button", {
        name: "Requirements, culture, signals and the role",
      }),
    );
    expect(details).toHaveTextContent(
      "Your profile states none of the criteria this screen reads.",
    );
    expect(details).toHaveTextContent("Nothing in the text stood out.");
    expect(details).toHaveTextContent(
      "This check reads postings in English or French",
    );
    expect(details).not.toHaveTextContent("not listed");
    expect(
      within(details).queryByRole("list", { name: "Ask before you commit" }),
    ).toBeNull();
  });

  it("shows a failed run's reason and keeps the assessment from before", async () => {
    openPosting(
      assessmentView({
        run: {
          id: "88888888-8888-4888-8888-888888888888",
          status: "failed",
          error: "The run stopped reporting and was marked failed.",
        },
      }),
    );
    const rail = await section();
    expect(await within(rail).findByRole("alert")).toHaveTextContent(
      "The last assessment did not finish. The run stopped reporting and was marked failed.",
    );
    expect(within(rail).getByTestId("assessment-summary")).toBeInTheDocument();
  });

  it("says when the agent ended without an assessment", async () => {
    openPosting(
      assessmentView({
        run: {
          id: "99999999-9999-4999-8999-999999999999",
          status: "done",
          error: null,
        },
        assessment: null,
      }),
    );
    const rail = await section();
    expect(
      await within(rail).findByText(
        "The agent ended without an assessment. Click Assess to try again.",
      ),
    ).toBeInTheDocument();
  });

  it("keeps the button off while a run is under way, and without a credential", async () => {
    openPosting(ASSESSING);
    await section();
    await waitFor(() => {
      expect(assessButton()).toHaveTextContent("Assessing");
    });
    expect(assessButton()).toBeDisabled();
  });

  it("explains a missing credential", async () => {
    openPosting(NEVER_ASSESSED);
    server.use(
      http.get("*/api/me/model-credential", () =>
        HttpResponse.json({ detail: "none" }, { status: 404 }),
      ),
    );
    const rail = await section();
    expect(
      await within(rail).findByText(/none is saved yet/),
    ).toBeInTheDocument();
    expect(assessButton()).toBeDisabled();
    expect(assessButton()).toHaveAccessibleDescription(
      /Agents run with your own model credential/,
    );
  });

  it("says why a click did not start", async () => {
    openPosting(NEVER_ASSESSED);
    server.use(
      http.post("*/api/jobs/:id/assess", () =>
        HttpResponse.json(
          {
            detail:
              "You already have 4 runs queued or running. Wait for one to end.",
          },
          { status: 429 },
        ),
      ),
    );
    await section();
    await waitFor(() => {
      expect(assessButton()).toBeEnabled();
    });
    await userEvent.click(assessButton());
    expect(
      await screen.findByText(/The assessment did not start\./),
    ).toBeInTheDocument();
    expect(assessButton()).toHaveTextContent("Assess");
  });
});
