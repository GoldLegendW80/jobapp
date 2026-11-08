import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { fakeSession, member } from "@/test/auth";
import { fakeDeck, makeCard } from "@/test/deck";
import { fakeJobsApi, makeJob } from "@/test/jobs";
import { fakeProfile } from "@/test/profile";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

const reasons = () =>
  within(screen.getByTestId("pass-reason")).getByRole("radiogroup", {
    name: "Why the Pass? Optional.",
  });

/** The deck of three cards, with the reason's route answering `status` and recording each call. */
async function open(status = 200) {
  fakeJobsApi([makeJob(1)]);
  fakeProfile();
  const deck = fakeDeck([makeCard(1), makeCard(2), makeCard(3)]);
  const sent: { id: string; reason: unknown }[] = [];
  server.use(
    http.put("*/api/postings/:id/pass/reason", async ({ params, request }) => {
      const body = (await request.json()) as { reason: string };
      sent.push({ id: String(params.id), reason: body.reason });
      return status === 200
        ? HttpResponse.json({ id: params.id, reason: body.reason })
        : HttpResponse.json(
            { detail: "This posting changed since you saw it." },
            { status },
          );
    }),
  );
  renderApp("/discover");
  await screen.findByTestId("deck-card");
  return { deck, sent };
}

beforeEach(() => {
  vi.restoreAllMocks();
  toast.dismiss();
  window.localStorage.clear();
  fakeSession(member);
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
});

describe("the reason of a Pass", () => {
  it("is offered after a Pass only, and one tap records it on that Pass", async () => {
    const { sent } = await open();
    expect(screen.queryByTestId("pass-reason")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Pass" }));
    await userEvent.click(
      within(reasons()).getByRole("radio", { name: "Pay too low" }),
    );
    await waitFor(() => {
      expect(sent).toEqual([{ id: makeCard(1).id, reason: "pay" }]);
    });
    expect(
      within(reasons()).getByRole("radio", { name: "Pay too low" }),
    ).toBeChecked();
  });

  it("never holds the swipe: the next card can be passed at once, and its reason is its own", async () => {
    const { deck, sent } = await open();
    await userEvent.click(screen.getByRole("button", { name: "Pass" }));
    await waitFor(() => {
      expect(deck.calls).toContain(`POST pass ${makeCard(1).id}`);
    });
    await userEvent.click(screen.getByRole("button", { name: "Pass" }));
    await waitFor(() => {
      expect(deck.calls).toContain(`POST pass ${makeCard(2).id}`);
    });
    const remote = within(reasons()).getByRole("radio", {
      name: "Not remote enough",
    });
    expect(remote).not.toBeChecked();
    await userEvent.click(remote);
    await waitFor(() => {
      expect(sent).toEqual([{ id: makeCard(2).id, reason: "remote" }]);
    });
  });

  it("goes with the undo of the Pass", async () => {
    await open();
    await userEvent.click(screen.getByRole("button", { name: "Pass" }));
    expect(await screen.findByTestId("pass-reason")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: /Undo last/ }));
    await waitFor(() => {
      expect(screen.queryByTestId("pass-reason")).toBeNull();
    });
  });

  it("says a reason that was not saved, and shows none chosen", async () => {
    await open(409);
    await userEvent.click(screen.getByRole("button", { name: "Pass" }));
    const pay = within(reasons()).getByRole("radio", { name: "Pay too low" });
    await userEvent.click(pay);
    expect(await screen.findByText(/The reason was not saved/)).toBeVisible();
    expect(pay).not.toBeChecked();
  });
});
