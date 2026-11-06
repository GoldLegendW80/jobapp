import { screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { fakeQueue, queueItem } from "@/test/apply-queue";
import { fakeSession, member } from "@/test/auth";
import { renderApp } from "@/test/render";

beforeEach(() => {
  fakeSession(member);
});

describe("a ticked Apply item in the review queue (A20)", () => {
  it("links to the job page's list and Confirm, never to an Approve the api refuses", async () => {
    const item = queueItem(1, "ready", {
      title: "Robot Arm Engineer",
      review_agent: true,
    });
    fakeQueue([item]);
    renderApp("/applications");
    const card = await screen.findByRole("article", {
      name: /Robot Arm Engineer/,
    });
    expect(
      within(card).getByRole("link", {
        name: "Check what the assistant filled and confirm",
      }),
    ).toHaveAttribute("href", `/jobs/${item.posting_id}`);
    expect(
      within(card).queryByRole("button", { name: "Approve and submit" }),
    ).toBeNull();
    expect(
      within(card).queryByRole("button", { name: "Review the answers" }),
    ).toBeNull();
  });
});
