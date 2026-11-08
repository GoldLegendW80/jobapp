import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RunRefusals } from "@/components/shared/ingestion/run-refusals";

describe("RunRefusals", () => {
  it("shows one line per section and rule, in the order given", () => {
    render(
      <RunRefusals
        refused={[
          {
            section: "experience",
            rule: "month_apart_from_year",
            count: 2,
            sentence: "for the assistant",
          },
          {
            section: "skills",
            rule: "unknown_field",
            count: 14,
            sentence: "for the assistant",
          },
        ]}
      />,
    );
    const lines = within(
      screen.getByRole("list", { name: "What was not kept" }),
    ).getAllByRole("listitem");
    expect(lines.map((line) => line.textContent)).toEqual([
      "2 jobs and schools were not kept: a month and its year are not on the same line of your document.",
      "14 skills were not kept: the assistant added a field this part of your profile does not have.",
    ]);
    expect(screen.queryByText(/for the assistant/)).toBeNull();
  });

  it("renders nothing when everything was kept", () => {
    const { container } = render(<RunRefusals refused={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
