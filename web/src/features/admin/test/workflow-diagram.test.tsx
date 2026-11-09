import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import { AGENT_FLOWS } from "@/design-system/fixtures/agent-flows";

import type { AgentFlow, FlowNode } from "../api/agents-api";
import {
  WorkflowDiagram,
  type NodeRun,
} from "@/components/shared/agents/workflow-diagram";
import { flowSentences } from "../helpers/flow-sentences";

const node = (
  id: string,
  kind: FlowNode["kind"],
  label: string,
  column: number,
  row: number,
  extra: Partial<FlowNode> = {},
): FlowNode => ({
  id,
  kind,
  label,
  column,
  row,
  note: "",
  group: "",
  ...extra,
});

const FLOW: AgentFlow = {
  nodes: [
    node("click", "person", "Save and start search", 0, 0),
    node("tick", "system", "Scheduled sweeps", 0, 1),
    node("sweep", "system", "Sweep", 1, 0),
    node("browser_discovery", "agent", "Website search", 2, 0),
    node("postings", "data", "Job postings", 2, 1),
    node("alone", "agent", "Proposal writer", 0, 3, {
      note: "Not started by the app yet",
    }),
  ],
  edges: [
    { from: "click", to: "sweep", label: "starts a sweep" },
    { from: "tick", to: "sweep", label: "starts one when due" },
    { from: "sweep", to: "browser_discovery", label: "one run per website" },
    { from: "browser_discovery", to: "postings", label: "stores them" },
    { from: "postings", to: "sweep", label: "read again" },
    { from: "sweep", to: "gone", label: "a box the api left out" },
  ],
};

function draw(flow: AgentFlow = FLOW, runs?: Record<string, NodeRun>) {
  return render(
    <MemoryRouter>
      <WorkflowDiagram title="Finding jobs" flow={flow} runs={runs} />
    </MemoryRouter>,
  );
}

// The connectors' own drawing, under the cards: not the icons.
const connectors = (container: HTMLElement) => [
  ...container.querySelectorAll("svg.absolute > g > path"),
];

/** The pairs of cards a flow's connectors join, a group's agents counted as their card. */
function connectorPairs(flow: AgentFlow): string[] {
  const card = new Map(
    flow.nodes.map((item) => [
      item.id,
      item.group ? `group:${item.group}` : item.id,
    ]),
  );
  return flow.edges.flatMap((edge) => {
    const from = card.get(edge.from);
    const to = card.get(edge.to);
    return from && to && from !== to ? [`${from} ${to}`] : [];
  });
}

describe("the workflow diagram", () => {
  it("names the workflow and its triggers, and links each agent's card by its plain name", () => {
    const { container } = draw();
    const figure = screen.getByRole("figure", {
      name: "Finding jobs, workflow",
    });
    expect(
      within(figure).getByText("on: Save and start search, Scheduled sweeps"),
    ).toBeInTheDocument();
    expect(
      within(figure).getByRole("link", { name: "Website search" }),
    ).toHaveAttribute("href", "/admin/agents/browser_discovery");
    expect(
      within(figure).getByRole("link", { name: /^Proposal writer/ }),
    ).toHaveTextContent("Not started by the app yet");
    const shapes = new Map(
      [...container.querySelectorAll<HTMLElement>("[data-kind]")].map((box) => [
        box.dataset.kind,
        box.className,
      ]),
    );
    expect(shapes.get("person")).toContain("rounded-full");
    expect(shapes.get("system")).toContain("border-dashed");
    expect(shapes.get("data")).toContain("border-double");
    // Each card carries its kind's icon, so no kind is told by its color alone.
    for (const box of container.querySelectorAll("[data-kind]"))
      expect(box.querySelector("svg.lucide")).not.toBeNull();
    // Only the agents are reachable: the other cards repeat the sentences below.
    for (const box of container.querySelectorAll(
      '[data-kind]:not([data-kind="agent"])',
    ))
      expect(box).toHaveAttribute("aria-hidden", "true");
  });

  it("joins cards with a dot at each end, a return dashed, and no line to a card it does not have", () => {
    const { container } = draw();
    const paths = connectors(container);
    expect(paths).toHaveLength(5);
    expect(container.querySelectorAll("svg.absolute circle")).toHaveLength(10);
    const dashed = paths.filter((path) =>
      path.getAttribute("stroke-dasharray"),
    );
    expect(dashed).toHaveLength(1);
    // Out of Job postings' right side (column 2, row 1), under it, into Sweep from the left.
    expect(dashed[0]?.getAttribute("d")).toMatch(
      /^M 948 110 H 956 Q 964 110 964 118 V 134 .* H 356$/,
    );
    expect(container.querySelector("marker")).toBeNull();
  });

  it("gives a screen reader each connector as a sentence, and the legend in words", () => {
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
      "From Scheduled sweeps to Sweep: starts one when due.",
      "From Sweep to Website search: one run per website.",
      "From Website search to Job postings: stores them.",
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

  it("shows the agent's registered name and what starts it in its tooltip", async () => {
    const user = userEvent.setup();
    draw();
    await user.hover(screen.getByRole("link", { name: "Website search" }));
    const tip = await screen.findByRole("tooltip");
    expect(tip).toHaveTextContent("browser_discovery");
    expect(tip).toHaveTextContent("From Sweep: one run per website");
  });

  it("draws agents that run side by side as one card that opens on its list", async () => {
    const user = userEvent.setup();
    const { container } = draw(AGENT_FLOWS.documents);
    const panel = screen.getByRole("button", { name: /Hiring panel/ });
    expect(panel).toHaveAttribute("aria-expanded", "false");
    expect(panel).toHaveTextContent("7 agents");
    expect(screen.queryByRole("link", { name: /recruiter/ })).toBeNull();
    // One connector per pair of cards: the seven agents' lines become one in and one out.
    expect(container.querySelectorAll("svg.absolute > g")).toHaveLength(
      new Set(connectorPairs(AGENT_FLOWS.documents)).size,
    );
    expect(connectorPairs(AGENT_FLOWS.documents).length).toBeGreaterThan(
      container.querySelectorAll("svg.absolute > g").length + 10,
    );
    await user.click(panel);
    expect(panel).toHaveAttribute("aria-expanded", "true");
    expect(
      screen.getByRole("link", { name: "reader, the recruiter" }),
    ).toHaveAttribute("href", "/admin/agents/documents_panel_reader_recruiter");
    expect(
      flowSentences(AGENT_FLOWS.documents).filter((line) =>
        line.endsWith("reviews, three at once."),
      ),
    ).toHaveLength(7);
  });

  it("puts a run's status in place of the kind's icon, its time on the right, and its link", async () => {
    const user = userEvent.setup();
    draw(AGENT_FLOWS.documents, {
      documents_cv: { status: "success", duration: "1m 48s", href: "/runs/1" },
      documents_panel_reader_rival: { status: "failure", duration: "38s" },
      documents_panel_screen: { status: "success", duration: "21s" },
    });
    const cv = screen.getByRole("link", { name: /^Succeeded,\s*CV writer/ });
    expect(cv).toHaveAttribute("href", "/runs/1");
    expect(cv).toHaveTextContent("Succeeded, CV writer1m 48s");
    expect(cv.querySelector('[data-status="success"]')).not.toBeNull();
    expect(cv.querySelector(".lucide-bot")).toBeNull();
    // The group shows its failed agent's status.
    const panel = screen.getByRole("button", { name: /Hiring panel/ });
    expect(panel.querySelector('[data-status="failure"]')).not.toBeNull();
    await user.click(panel);
    expect(
      screen.getByRole("link", { name: /reader, another applicant/ }),
    ).toHaveTextContent("Failed, reader, another applicant38s");
    const run = screen.getByRole("list", { name: "Finding jobs, this run" });
    expect(
      within(run).getByText("CV writer: Succeeded, 1m 48s."),
    ).toBeVisible();
  });

  it("zooms with minus and plus and fits again, inside a frame that never scrolls", async () => {
    const user = userEvent.setup();
    const { container } = draw();
    const frame = screen.getByRole("group", {
      name: "Finding jobs, workflow drawing",
    });
    expect(frame).toHaveClass("overflow-hidden");
    expect(frame).toHaveAttribute("tabindex", "0");
    const drawing = container.querySelector<HTMLElement>("[data-drawing]");
    // Three columns of 260 pixels, 332 apart, inside 24 on each side.
    expect(drawing).toHaveStyle({ width: "972px" });
    const scale = () =>
      /scale\(([\d.]+)\)/.exec(drawing?.style.transform ?? "")?.[1];
    expect(scale()).toBe("1");
    await user.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(scale()).toBe("1.25");
    await user.click(screen.getByRole("button", { name: "Zoom out" }));
    await user.click(screen.getByRole("button", { name: "Zoom out" }));
    expect(scale()).toBe("0.8");
    await user.click(screen.getByRole("button", { name: "Fit the drawing" }));
    expect(scale()).toBe("1");
    frame.focus();
    await user.keyboard("{ArrowLeft}");
    expect(drawing?.style.transform).toContain("translate(48px, 0px)");
  });

  it("says nothing with a dash as a separator, in every workflow the api serves", () => {
    for (const flow of Object.values(AGENT_FLOWS)) {
      const { container, unmount } = draw(flow);
      expect(container.textContent).not.toMatch(/[–—]| - /);
      unmount();
    }
  });
});
