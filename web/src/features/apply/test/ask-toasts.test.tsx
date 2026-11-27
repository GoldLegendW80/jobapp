import { act, screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SIGNIN_ASK, WAIT_NOW } from "@/design-system/fixtures/agent-wait";
import type { Question } from "@/features/apply/api/apply-api";
import { claimRun } from "@/features/agents/helpers/run-ends";
import { queueItem } from "@/test/apply-queue";
import { fakeSession, member } from "@/test/auth";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import { needsCountKey } from "../api/queue-api";
import { ASKS_KEY } from "../api/use-ask-toasts";

const ASKING = queueItem(1, "needs_person", {
  title: "Robot Arm Engineer",
  mode: "direct",
  needs: "agent_waits",
});
const ASKED_KEY = `${ASKING.id}:${SIGNIN_ASK.until ?? ""}`;
// Read after ASKING: its toast shows once the hook is done with ASKING, so an absence is proved.
const LATER = queueItem(2, "needs_person", {
  title: "Drone Pilot",
  mode: "direct",
  needs: "agent_waits",
});

/** The count and the queue as the api answers them now; `needs` and `items` move a test on. */
function fakeNeeds() {
  const state = {
    needs: 0,
    items: [ASKING],
    questions: {} as Record<string, Question>,
    queueReads: 0,
    detailReads: 0,
  };
  server.use(
    http.get("*/api/apply/queue/count", () =>
      HttpResponse.json({
        waiting_on_you: state.needs,
        needs_you: state.needs,
      }),
    ),
    http.get("*/api/apply/queue", () => {
      state.queueReads += 1;
      return HttpResponse.json({
        items: state.items,
        waiting_on_you: state.needs,
      });
    }),
    http.get("*/api/applications/:id", ({ params }) => {
      state.detailReads += 1;
      return HttpResponse.json({
        ...state.items.find((item) => item.id === params.id),
        events: [],
        question: state.questions[String(params.id)] ?? SIGNIN_ASK,
        chat: [],
        now: WAIT_NOW,
      });
    }),
  );
  return state;
}

const toasts = () => document.querySelectorAll("[data-sonner-toast]");

async function openAt(path: string) {
  const app = renderApp(path);
  await screen.findByRole("navigation", { name: "Main" });
  await vi.waitFor(() => {
    expect(app.client.getQueryData(needsCountKey)).toBeDefined();
  });
  await act(async () => {});
  return app;
}

/** LATER's toast, the last the hook can show: every item before it was read and decided. */
async function laterShown() {
  expect(await screen.findByText(/^Drone Pilot at /)).toBeVisible();
}

async function readCount(app: Awaited<ReturnType<typeof openAt>>) {
  await act(() => app.client.refetchQueries({ queryKey: needsCountKey }));
}

beforeEach(() => {
  window.localStorage.clear();
  fakeSession(member);
});

afterEach(() => {
  toast.dismiss();
});

describe("the toast The assistant needs you (A25)", () => {
  it("names the posting and links to its application once per ask, on another page", async () => {
    const state = fakeNeeds();
    const app = await openAt("/activity");
    expect(toasts()).toHaveLength(0);
    expect(state.queueReads).toBe(0);

    state.needs = 1;
    await readCount(app);
    const title = await screen.findByText("The assistant needs you");
    const shown = within(
      title.closest<HTMLElement>("[data-sonner-toast]") as HTMLElement,
    );
    expect(
      shown.getByText(
        "Robot Arm Engineer at Example Robotics: it waits for your answer or for you in the browser.",
      ),
    ).toBeInTheDocument();
    expect(
      shown.getByRole("link", { name: "Open the application" }),
    ).toHaveAttribute("href", `/jobs/${ASKING.posting_id}`);
    // The assistant's sentence is the model's words, which a page can steer: never in a toast.
    expect(
      title.closest<HTMLElement>("[data-sonner-toast]"),
    ).not.toHaveTextContent(SIGNIN_ASK.sentence);

    // The same ask at the next reads: no second toast, and no read of the queue.
    await readCount(app);
    await readCount(app);
    expect(toasts()).toHaveLength(1);
    expect(state.queueReads).toBe(1);
  });

  it("tells of an ask that waits when the page opens", async () => {
    const state = fakeNeeds();
    state.needs = 1;
    await openAt("/jobs");
    expect(await screen.findByText("The assistant needs you")).toBeVisible();
  });

  it("leaves out an ask another tab of this browser told of", async () => {
    claimRun(ASKED_KEY, undefined, ASKS_KEY);
    const state = fakeNeeds();
    state.items = [ASKING, LATER];
    state.needs = 2;
    await openAt("/jobs");
    await laterShown();
    expect(toasts()).toHaveLength(1);
    expect(state.detailReads).toBe(2);
  });

  it("is not shown for an ask that ended before the read", async () => {
    const state = fakeNeeds();
    state.items = [ASKING, LATER];
    state.questions[ASKING.id] = { ...SIGNIN_ASK, live: false };
    state.needs = 2;
    await openAt("/jobs");
    await laterShown();
    expect(toasts()).toHaveLength(1);
  });

  it("is not shown for an application that needs the person for anything else", async () => {
    const state = fakeNeeds();
    state.items = [queueItem(3, "needs_person", { needs: "captcha" }), LATER];
    const app = await openAt("/jobs");
    state.needs = 2;
    await readCount(app);
    await laterShown();
    expect(toasts()).toHaveLength(1);
  });
});
