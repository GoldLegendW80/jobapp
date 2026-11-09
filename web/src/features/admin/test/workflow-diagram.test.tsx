import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import { AGENT_FLOWS } from "@/design-system/fixtures/agent-flows";

import type { AgentFlow } from "../api/agents-api";
import { WorkflowDiagram } from "../components/workflow-diagram";
import { flowSentences } from "../helpers/flow-sentences";

const FLOW: AgentFlow = {
  nodes: [
    {
      id: "click",
      kind: "person",
      label: "Save and start search",
      column: 0,
      row: 0,
      note: "",
    },
    {
      id: "sweep",
      kind: "system",
      label: "Sweep",
      column: 1,
      row: 0,
      note: "",
    },
    {
      id: "documents_panel_reader_recruiter",
      kind: "agent",
      label: "documents_panel_reader_recruiter",
      column: 2,
      row: 0,
      note: "",
    },
    {
      id: "postings",
      kind: "data",
      label: "Job postings",
      column: 2,
      row: 1,
      note: "",
    },
    {
      id: "alone",
      kind: "agent",
      label: "alone",
      column: 0,
      row: 2,
      note: "Not started by the app yet",
    },
  ],
  edges: [
    { from: "click", to: "sweep", label: "starts a sweep" },
    {
      from: "sweep",
      to: "documents_panel_reader_recruiter",
      label: "one run each",
    },
    {
      from: "documents_panel_reader_recruiter",
      to: "postings",
      label: "stores them",
    },
    { from: "postings", to: "sweep", label: "read again" },
    { from: "sweep", to: "gone", label: "a box the api left out" },
  ],
};

function draw(flow: AgentFlow = FLOW) {
  return render(
    <MemoryRouter>
      <WorkflowDiagram title="Finding jobs" flow={flow} />
    </MemoryRouter>,
  );
}

describe("the workflow diagram", () => {
  it("links each agent to its page and tells each kind of box by shape and icon", () => {
    const { container } = draw();
    const figure = screen.getByRole("figure", {
      name: "Finding jobs, workflow",
    });
    const link = within(figure).getByRole("link", {
      name: "documents_panel_reader_recruiter",
    });
    expect(link).toHaveAttribute(
      "href",
      "/admin/agents/documents_panel_reader_recruiter",
    );
    // A long name breaks after its underscores, never in the middle of a word.
    expect(link.querySelectorAll("wbr")).toHaveLength(3);
    expect(
      within(figure).getByRole("link", { name: /^alone/ }),
    ).toHaveTextContent("Not started by the app yet");
    const shapes = new Map(
      [...container.querySelectorAll<HTMLElement>("[data-kind]")].map((box) => [
        box.dataset.kind,
        box.className,
      ]),
    );
    expect(shapes.get("agent")).toContain("rounded-xl");
    expect(shapes.get("person")).toContain("rounded-full");
    expect(shapes.get("system")).toContain("border-dashed");
    expect(shapes.get("data")).toContain("border-double");
    // Each box carries its own icon, so no kind is told by its color alone.
    for (const box of container.querySelectorAll("[data-kind]"))
      expect(box.querySelector("svg.lucide")).not.toBeNull();
    // Only the agents are reachable: the other boxes repeat the sentences below.
    for (const box of container.querySelectorAll(
      '[data-kind]:not([data-kind="agent"])',
    ))
      expect(box).toHaveAttribute("aria-hidden", "true");
  });

  it("draws a return to an earlier column dashed, and no arrow to a box it does not have", () => {
    const { container } = draw();
    // The arrows' own drawing, under the boxes: neither the icons nor the arrowhead.
    const paths = [...container.querySelectorAll("svg.absolute > path")];
    expect(paths).toHaveLength(4);
    const dashed = paths.filter((path) =>
      path.getAttribute("stroke-dasharray"),
    );
    expect(dashed).toHaveLength(1);
    // From the bottom of Job postings (row 1) to the bottom of Sweep (row 0), dipping under both.
    expect(dashed[0].getAttribute("d")).toMatch(
      /^M [\d.]+ 176 C [\d.]+ 208, [\d.]+ 208, [\d.]+ 80$/,
    );
    for (const path of paths)
      expect(path.getAttribute("marker-end")).toMatch(/^url\(#flow-arrow-/);
  });

  it("gives a screen reader each arrow as a sentence, and the legend in words", () => {
    draw();
    const list = screen.getByRole("list", {
      name: "Finding jobs, each step in words",
    });
    expect(
      within(list)
        .getAllByRole("listitem")
        .map((item) => item.textContent),
    ).toEqual([
      "From Save and start search to Sweep: starts a sweep.",
      "From Sweep to documents_panel_reader_recruiter: one run each.",
      "From documents_panel_reader_recruiter to Job postings: stores them.",
      "From Job postings back to Sweep: read again.",
    ]);
    for (const words of [
      "Agent: opens its page",
      "A person: a click, a message, an upload",
      "The app: a sweep, a tick, a job",
      "Stored data another step reads",
      "Starts or feeds",
      "Goes back to an earlier step",
    ])
      expect(screen.getByText(words)).toBeInTheDocument();
  });

  it("scrolls sideways inside its own frame, at the width its columns need", () => {
    draw();
    const frame = screen.getByRole("group", {
      name: "Finding jobs, workflow drawing",
    });
    expect(frame).toHaveClass("overflow-x-auto");
    expect(frame).toHaveAttribute("tabindex", "0");
    // Three columns of 168 pixels, 256 apart, inside 24 on each side.
    expect(frame.firstElementChild).toHaveStyle({ width: "728px" });
  });

  it("draws the fan of the hiring panel's reviewers with its words once", () => {
    draw(AGENT_FLOWS.documents);
    const figure = screen.getByRole("figure", {
      name: "Finding jobs, workflow",
    });
    expect(within(figure).getAllByText("reviews, three at once")).toHaveLength(
      1,
    );
    expect(within(figure).getAllByText("sends its review")).toHaveLength(1);
    expect(
      flowSentences(AGENT_FLOWS.documents).filter((line) =>
        line.endsWith("reviews, three at once."),
      ),
    ).toHaveLength(7);
  });

  it("says nothing with a dash as a separator, in every workflow the api serves", () => {
    for (const flow of Object.values(AGENT_FLOWS)) {
      const { container, unmount } = draw(flow);
      expect(container.textContent).not.toMatch(/[–—]| - /);
      unmount();
    }
  });
});
