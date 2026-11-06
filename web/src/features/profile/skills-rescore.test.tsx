import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";
import { afterEach, describe, expect, it } from "vitest";

import { admin, fakeSession } from "@/test/auth";
import { fakeProfile, haraldLike } from "@/test/profile";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

afterEach(() => {
  toast.dismiss();
});

const WAIT = { timeout: 4000 };

function spend(overrides: Record<string, unknown> = {}) {
  const asked: unknown[] = [];
  server.use(
    http.post("*/api/documents/spend/skills", async ({ request }) => {
      asked.push(await request.json());
      return HttpResponse.json({
        auto: true,
        min_fit: 70,
        writes: true,
        newly: 3,
        newly_estimate: {
          tokens: { low: 30000, high: 60000 },
          cost: { currency: "USD", low: "0.40", high: "0.80" },
        },
        priced: true,
        cost_note: "Estimates from list prices.",
        ...overrides,
      });
    }),
  );
  return asked;
}

async function openSkills() {
  fakeSession(admin);
  const state = fakeProfile(haraldLike());
  const user = userEvent.setup();
  renderApp("/profile?tab=skills");
  await screen.findByRole("form", { name: "Skills" });
  return { user, state };
}

async function changeYears(user: ReturnType<typeof userEvent.setup>) {
  const years = screen.getByLabelText("Years of experience");
  await user.clear(years);
  await user.type(years, "6");
}

describe("the rescore after the save", () => {
  function status(answers: Record<string, unknown>[]) {
    let call = 0;
    server.use(
      http.get("*/api/jobs/rescore/status", () => {
        const answer = answers[Math.min(call, answers.length - 1)];
        call += 1;
        return HttpResponse.json({
          state: "idle",
          wanted_revision: 7,
          done_revision: 6,
          finished_at: null,
          outcome: null,
          ...answer,
        });
      }),
    );
  }

  it("says it is rescoring, then how many postings now fit, with a link to them", async () => {
    spend({ newly: 0 });
    status([
      { state: "running" },
      {
        done_revision: 7,
        finished_at: "2026-10-09T08:00:05Z",
        outcome: {
          scored: 40,
          changed: 9,
          up: 4,
          down: 1,
          threshold: 70,
          no_skills: false,
          failed: false,
        },
      },
    ]);
    const { user, state } = await openSkills();
    await changeYears(user);
    await user.click(screen.getByRole("button", { name: "Save skills" }));
    expect(state.saves).toHaveLength(1);
    expect(await screen.findByText("Rescoring your postings")).toBeVisible();
    const line = await screen.findByText(
      /4 more postings now fit you at 70 or more\. 1 posting no longer does\./,
      {},
      WAIT,
    );
    expect(line).toHaveAttribute("role", "status");
    expect(
      screen.getByRole("link", { name: "See them in Jobs" }),
    ).toHaveAttribute("href", "/jobs?fit_min=70&sort=fit");
  });

  it("says nothing when the save changed no skill", async () => {
    spend({ newly: 0 });
    status([{ wanted_revision: 5, done_revision: 5 }]);
    const { user } = await openSkills();
    await changeYears(user);
    await user.click(screen.getByRole("button", { name: "Save skills" }));
    await screen.findByText("Skills saved.");
    expect(screen.queryByTestId("rescore-line")).not.toBeInTheDocument();
  });

  it("reads the rescore again after each save, and says the way out when it failed", async () => {
    spend({ newly: 0 });
    status([{ wanted_revision: 7, done_revision: 7, outcome: null }]);
    const { user } = await openSkills();
    await changeYears(user);
    await user.click(screen.getByRole("button", { name: "Save skills" }));
    await screen.findByTestId("rescore-line", {}, WAIT);
    status([
      {
        done_revision: 8,
        wanted_revision: 8,
        outcome: {
          scored: 0,
          changed: 0,
          up: 0,
          down: 0,
          threshold: 70,
          no_skills: false,
          failed: true,
        },
      },
    ]);
    await user.clear(screen.getByLabelText("Years of experience"));
    await user.type(screen.getByLabelText("Years of experience"), "2");
    await user.click(screen.getByRole("button", { name: "Save skills" }));
    expect(
      await screen.findByText(
        "Your postings were not rescored. Use Rescore all postings in Jobs.",
        {},
        WAIT,
      ),
    ).toBeVisible();
  });
});
