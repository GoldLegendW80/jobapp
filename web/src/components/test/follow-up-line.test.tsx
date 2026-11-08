import { screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import { FollowUpLine } from "@/components/shared/apply/follow-up-line";
import {
  cold,
  fakeFollowUps,
  followUp,
} from "@/design-system/fixtures/follow-ups";
import { followUpsKey } from "@/features/apply/api/followups-api";
import { renderWithQuery } from "@/test/render";

const line = (jobId: string) =>
  renderWithQuery(
    <MemoryRouter>
      <FollowUpLine jobId={jobId} />
    </MemoryRouter>,
  );

describe("the follow up line of a posting", () => {
  it("says the follow up is due and leads to Applications", async () => {
    fakeFollowUps([followUp()]);
    line("posting-fu-1");
    expect(
      await screen.findByText("First follow up due since 4 Nov 2026."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Write it in Applications" }),
    ).toHaveAttribute("href", "/applications");
  });

  it("offers nothing more after two follow ups without an answer", async () => {
    fakeFollowUps([cold()]);
    line("posting-fu-3");
    expect(
      await screen.findByText(
        "No answer after two follow ups. The app suggests no third one.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("is absent for a posting that is not followed, or while follow ups are off", async () => {
    fakeFollowUps([followUp()], false);
    const { container, client } = line("posting-fu-1");
    await waitFor(() => {
      expect(client.getQueryState(followUpsKey)?.status).toBe("success");
    });
    expect(container).toBeEmptyDOMElement();
  });
});
