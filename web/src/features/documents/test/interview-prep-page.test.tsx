import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import {
  NOT_PREPARED,
  fakeInterviewPrep,
  preparedFor,
} from "@/design-system/fixtures/interview-prep";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";
import { fakeSession, member } from "@/test/auth";
import { renderApp } from "@/test/render";

const JOB = { ...POSTING_DETAIL, status: "interview" as const };
const PAGE = `/jobs/${JOB.id}/interview`;

describe("the interview preparation page", () => {
  it("prepares on a click and shows the questions, each tagged with where it comes from", async () => {
    fakeSession(member);
    fakeInterviewPrep(JOB, NOT_PREPARED, { consent: { accepted: true } });
    const user = userEvent.setup();
    renderApp(PAGE);

    expect(
      await screen.findByRole("heading", {
        level: 1,
        name: "Interview preparation",
      }),
    ).toBeVisible();
    await user.click(
      await screen.findByRole("button", { name: "Prepare for the interview" }),
    );
    const questions = await screen.findByRole("region", {
      name: "Questions to expect",
    });
    expect(questions).toHaveTextContent("Nobody reported them.");
    expect(questions).toHaveTextContent("From the hiring panel");
    expect(questions).toHaveTextContent("From the posting");
    expect(questions).not.toHaveTextContent(/they asked/i);
  });

  it("asks for the consent before preparing when it is missing", async () => {
    fakeSession(member);
    fakeInterviewPrep(JOB, NOT_PREPARED, { consent: { accepted: false } });
    const user = userEvent.setup();
    renderApp(PAGE);

    await user.click(
      await screen.findByRole("button", { name: "Prepare for the interview" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "What is sent to prepare your interview",
    });
    await user.click(within(dialog).getByRole("checkbox"));
    await user.click(
      within(dialog).getByRole("button", { name: "Agree and prepare" }),
    );
    expect(
      await screen.findByRole("region", { name: "Your stories" }),
    ).toBeVisible();
  });

  it("proposes the ticked stories for the profile and accepts them only on the click", async () => {
    fakeSession(member);
    fakeInterviewPrep(JOB, preparedFor());
    const user = userEvent.setup();
    renderApp(PAGE);

    const stories = await screen.findByRole("region", { name: "Your stories" });
    const keep = within(stories).getByRole("button", {
      name: "Keep in my profile",
    });
    expect(keep).toBeDisabled();
    await user.click(
      within(stories).getByRole("checkbox", {
        name: "The freight pricing pipeline",
      }),
    );
    await user.click(keep);
    expect(
      await within(stories).findByText(/Nothing changes until you accept/),
    ).toBeVisible();
    await user.click(within(stories).getByRole("button", { name: "Accept" }));
    expect(
      await screen.findByRole("button", { name: "Keep in my profile" }),
    ).toBeVisible();
    expect(
      within(stories).queryByText(/Nothing changes until you accept/),
    ).toBeNull();
  });

  it("starts the practice and appends a debrief by hand", async () => {
    fakeSession(member);
    fakeInterviewPrep(JOB, preparedFor());
    const user = userEvent.setup();
    renderApp(PAGE);

    const practice = await screen.findByRole("region", { name: "Practice" });
    await user.click(
      within(practice).getByRole("button", { name: "Start practice" }),
    );
    expect(
      await within(practice).findByText(/walk me through a pipeline/),
    ).toBeVisible();
    expect(within(practice).getByLabelText("Your answer")).toBeVisible();

    const debrief = screen.getByRole("region", { name: "Debrief" });
    await user.type(
      within(debrief).getByLabelText("What they asked"),
      "Why we should hire you.",
    );
    await user.click(
      within(debrief).getByRole("button", { name: "Add to the debrief" }),
    );
    expect(
      await within(debrief).findByText("Why we should hire you."),
    ).toBeVisible();
  });
});
