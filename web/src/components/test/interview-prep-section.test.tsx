import { screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import { InterviewPrepSection } from "@/components/shared/documents/interview-prep/interview-prep-section";
import {
  NOT_PREPARED,
  fakeInterviewPrep,
  preparedFor,
} from "@/design-system/fixtures/interview-prep";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";
import { renderWithQuery } from "@/test/render";

describe("the Interview section of a posting", () => {
  it("offers Prepare first once invited, and on demand before", async () => {
    const invited = { ...POSTING_DETAIL, status: "interview" as const };
    fakeInterviewPrep(invited, NOT_PREPARED);
    renderWithQuery(
      <MemoryRouter>
        <InterviewPrepSection job={invited} />
      </MemoryRouter>,
    );
    expect(
      await screen.findByText(/You are invited to an interview/),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Prepare for the interview" }),
    ).toBeVisible();
  });

  it("links to the preparation once written, with its counts", async () => {
    fakeInterviewPrep(POSTING_DETAIL, preparedFor());
    renderWithQuery(
      <MemoryRouter>
        <InterviewPrepSection job={POSTING_DETAIL} />
      </MemoryRouter>,
    );
    expect(
      await screen.findByText("Ready: 3 questions, 2 stories."),
    ).toBeVisible();
    expect(
      screen.getByRole("link", { name: "Open the preparation" }),
    ).toHaveAttribute("href", `/jobs/${POSTING_DETAIL.id}/interview`);
  });
});
