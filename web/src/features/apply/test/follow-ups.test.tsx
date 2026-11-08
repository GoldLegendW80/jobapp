import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import {
  cold,
  fakeFollowUps,
  followUp,
  waiting,
  written,
} from "@/design-system/fixtures/follow-ups";
import { fakeQueue } from "@/test/apply-queue";
import { fakeSession, member } from "@/test/auth";
import { renderApp } from "@/test/render";

const section = async () =>
  (await screen.findByRole("heading", { name: /^Follow ups/ })).closest(
    "section",
  ) as HTMLElement;

describe("the follow ups of the Applications page", () => {
  beforeEach(() => {
    fakeSession(member);
    fakeQueue([]);
  });

  it("lists what is due first, then what waits, and counts the others", async () => {
    fakeFollowUps([followUp(), waiting(), cold()]);
    renderApp("/applications");
    const found = await section();
    expect(
      within(found).getByRole("heading", { name: "Follow ups (1 due)" }),
    ).toBeInTheDocument();
    const links = within(found).getAllByRole("link");
    expect(links.map((link) => link.textContent)).toEqual([
      "Data Engineer, Example Robotics",
      "Platform Engineer, Sample Logistics",
    ]);
    expect(
      within(found).getByText(/1 more application asks no follow up now/),
    ).toBeInTheDocument();
    expect(found.textContent).not.toMatch(/[–—]| - /);
  });

  it("writes a draft only on the click, then shows it to send from the person's mail", async () => {
    fakeFollowUps([followUp()]);
    renderApp("/applications");
    const found = await section();
    expect(within(found).queryByLabelText("Message")).toBeNull();
    await userEvent.click(
      within(found).getByRole("button", { name: "Write a draft" }),
    );
    const message = await within(found).findByLabelText("Message");
    expect((message as HTMLTextAreaElement).value).toMatch(
      /^Hello,\n\nI applied for the Data Engineer role/,
    );
    const open = within(found).getByRole("link", {
      name: "Open in your mail",
    });
    expect(open.getAttribute("href")).toMatch(
      /^mailto:\?subject=Data%20Engineer%3A/,
    );
  });

  it("records I sent it and moves the next date", async () => {
    fakeFollowUps([written()]);
    renderApp("/applications");
    const found = await section();
    await userEvent.click(
      await within(found).findByRole("button", { name: "I sent it" }),
    );
    await waitFor(() => {
      expect(
        within(found).getByText("Second follow up due on 14 Nov 2026."),
      ).toBeInTheDocument();
    });
    expect(within(found).getByText(/Follow ups sent: 1 of 2/)).toBeVisible();
  });

  it("keeps the person's own text, saved on their click", async () => {
    fakeFollowUps([written()]);
    renderApp("/applications");
    const found = await section();
    const subject = await within(found).findByLabelText("Subject");
    await userEvent.clear(subject);
    await userEvent.type(subject, "Following up");
    await userEvent.click(within(found).getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(
        within(found).getByRole("link", { name: "Open in your mail" }),
      ).toHaveAttribute(
        "href",
        expect.stringMatching(/subject=Following%20up&/),
      );
    });
  });

  it("turns follow ups off with the switch", async () => {
    fakeFollowUps([followUp()]);
    renderApp("/applications");
    const found = await section();
    await userEvent.click(within(found).getByRole("switch"));
    expect(
      await within(found).findByText(
        "Follow ups are off: no reminder and no draft.",
      ),
    ).toBeInTheDocument();
    expect(
      within(found).queryByRole("button", { name: "Write a draft" }),
    ).toBeNull();
  });

  it("shows nothing when no posting is followed", async () => {
    fakeFollowUps([]);
    renderApp("/applications");
    await screen.findByText("Your queue is empty");
    expect(screen.queryByRole("heading", { name: /^Follow ups/ })).toBeNull();
  });
});
