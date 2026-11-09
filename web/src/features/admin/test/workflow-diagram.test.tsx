import { fireEvent, render, screen, within } from "@testing-library/react";
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

  it("gives a step nothing feeds its whole label in its tooltip, for a label its card cuts", async () => {
    const user = userEvent.setup();
    const { container } = draw();
    const card = container.querySelector('[data-card="click"] div');
    if (!card) throw new Error("no Save and start search card");
    await user.hover(card);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "Save and start search",
    );
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

  it("counts a step that ran several times, marks the selected one, and gives a group its total time", () => {
    render(
      <MemoryRouter>
        <WorkflowDiagram
          title="Documents"
          flow={AGENT_FLOWS.documents}
          runs={{
            documents_cv: {
              status: "success",
              duration: "3m 02s",
              href: "/activity/runs/1?step=a",
              count: 2,
            },
            documents_letter: { status: "success", duration: "52s", count: 1 },
            documents_panel_reader_ceo: {
              status: "running",
              duration: "54s",
              href: "/activity/runs/1?step=b",
            },
          }}
          groupRuns={{
            "Hiring panel": { status: "running", duration: "1m 40s" },
          }}
          selected="documents_panel_reader_ceo"
          linkAgents={false}
        />
      </MemoryRouter>,
    );
    // Rebuilt after the panel: two runs on one card, said in words; one run says no count.
    const cv = screen.getByRole("link", { name: /CV writer/ });
    expect(cv).toHaveTextContent("×2");
    expect(cv).toHaveAccessibleName(/2 runs/);
    expect(
      screen.getByText("Letter writer").closest("[data-card]"),
    ).not.toHaveTextContent("×");
    // The group shows its own time, not its count of agents, and opens on the step selected.
    const panel = screen.getByRole("button", { name: /Hiring panel/ });
    expect(panel).toHaveTextContent("1m 40s");
    expect(panel).not.toHaveTextContent("7 agents");
    expect(panel).toHaveAttribute("aria-expanded", "true");
    expect(panel.closest("[data-card]")).toHaveAttribute("data-selected");
    expect(screen.getByRole("link", { current: "step" })).toHaveTextContent(
      "reader, the CEO",
    );
    expect(cv).not.toHaveAttribute("aria-current");
    expect(cv.closest("[data-card]")).not.toHaveAttribute("data-selected");
    const run = screen.getByRole("list", { name: "Documents, this run" });
    expect(
      within(run).getByText("CV writer: Succeeded, 2 runs, 3m 02s."),
    ).toBeInTheDocument();
    expect(
      within(run).getByText("Hiring panel: Running, 1m 40s in all."),
    ).toBeInTheDocument();
  });

  it("marks the card of a step selected outside a group, and none when nothing is", () => {
    const { rerender } = render(
      <MemoryRouter>
        <WorkflowDiagram
          title="Documents"
          flow={AGENT_FLOWS.documents}
          runs={{ documents_cv: { status: "success", href: "/x" } }}
          selected="documents_cv"
          linkAgents={false}
        />
      </MemoryRouter>,
    );
    expect(screen.getByRole("link", { current: "step" })).toHaveTextContent(
      "CV writer",
    );
    expect(document.querySelectorAll("[data-selected]")).toHaveLength(1);
    expect(
      screen.getByRole("button", { name: /Hiring panel/ }),
    ).toHaveAttribute("aria-expanded", "false");
    rerender(
      <MemoryRouter>
        <WorkflowDiagram
          title="Documents"
          flow={AGENT_FLOWS.documents}
          runs={{ documents_cv: { status: "success", href: "/x" } }}
          linkAgents={false}
        />
      </MemoryRouter>,
    );
    expect(screen.queryByRole("link", { current: "step" })).toBeNull();
    expect(document.querySelectorAll("[data-selected]")).toHaveLength(0);
  });

  it("zooms with minus and plus and fits again, inside a frame that never scrolls", async () => {
    const user = userEvent.setup();
    const { container } = draw();
    const frame = screen.getByRole("group", {
      name: "Finding jobs, workflow drawing",
    });
    expect(frame).toHaveClass("overflow-hidden");
    expect(frame).toHaveAttribute("tabindex", "0");
    // The controls have their own place: none covers a card of the drawing.
    expect(frame).not.toContainElement(
      screen.getByRole("button", { name: "Zoom in" }),
    );
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

  it("moves the drawing both ways under one finger once zoomed, zooms under two, and gives the page its scroll back at Fit", async () => {
    const user = userEvent.setup();
    const { container } = draw(AGENT_FLOWS.documents);
    const frame = screen.getByRole("group", {
      name: "Finding jobs, workflow drawing",
    });
    const drawing = container.querySelector<HTMLElement>("[data-drawing]");
    const view = () =>
      /translate\((-?[\d.]+)px, (-?[\d.]+)px\) scale\(([\d.]+)\)/
        .exec(drawing?.style.transform ?? "")
        ?.slice(1)
        .map(Number) ?? [];
    const touch = (pointerId: number, isPrimary: boolean) => ({
      pointerId,
      isPrimary,
      pointerType: "touch",
      button: 0,
    });
    const finger = (pointerId: number, isPrimary = pointerId === 1) => ({
      down: (clientX: number, clientY: number) => {
        fireEvent.pointerDown(frame, {
          ...touch(pointerId, isPrimary),
          clientX,
          clientY,
        });
      },
      to: (clientX: number, clientY: number) => {
        fireEvent.pointerMove(frame, {
          ...touch(pointerId, isPrimary),
          clientX,
          clientY,
        });
      },
      up: () => {
        fireEvent.pointerUp(frame, touch(pointerId, isPrimary));
      },
    });
    // Fitted, a vertical swipe over the drawing scrolls the page.
    expect(frame.style.touchAction).toBe("pan-y");
    await user.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(frame.style.touchAction).toBe("none");
    const [x, y, scale] = view();
    // One finger up the frame: the drawing follows it, the lower rows come into view.
    const one = finger(1);
    one.down(200, 200);
    one.to(200, 140);
    one.to(170, 80);
    one.up();
    expect(view()).toEqual([(x ?? 0) - 30, (y ?? 0) - 120, scale]);
    // Two fingers apart: twice the size, the point between them stays under them.
    const [x1 = 0, y1 = 0, s1 = 1] = view();
    const [a, b] = [finger(1), finger(2)];
    a.down(100, 100);
    b.down(150, 100);
    b.to(200, 100);
    a.up();
    b.up();
    const [x2 = 0, y2 = 0, s2 = 1] = view();
    expect(s2).toBeCloseTo(Math.min(2, s1 * 2));
    expect(x2 + ((125 - x1) * s2) / s1).toBeCloseTo(150);
    expect(y2 + ((100 - y1) * s2) / s1).toBeCloseTo(100);
    // A gesture the browser took ends there: the next finger drags, it pinches nothing.
    const taken = finger(1);
    taken.down(50, 50);
    fireEvent.pointerCancel(frame, touch(1, true));
    const next = finger(3, false);
    next.down(50, 50);
    next.to(50, 10);
    next.up();
    expect(view()).toEqual([x2, y2 - 40, s2]);
    // However far a finger goes, a corner of the drawing stays in the frame.
    const far = finger(1);
    far.down(200, 200);
    far.to(-9000, -9000);
    far.up();
    const [xFar = 0, yFar = 0, sFar = 1] = view();
    expect(xFar + parseFloat(drawing?.style.width ?? "") * sFar).toBe(48);
    expect(yFar + parseFloat(drawing?.style.height ?? "") * sFar).toBe(48);
    await user.click(screen.getByRole("button", { name: "Fit the drawing" }));
    expect(frame.style.touchAction).toBe("pan-y");
    // A finger on a fitted drawing is the page's: the drawing stays as it is.
    const fit = view();
    const swipe = finger(1);
    swipe.down(100, 100);
    swipe.to(40, 60);
    swipe.up();
    expect(view()).toEqual(fit);
    expect(frame.style.touchAction).toBe("pan-y");
  });

  it("says nothing with a dash as a separator, in every workflow the api serves", () => {
    for (const flow of Object.values(AGENT_FLOWS)) {
      const { container, unmount } = draw(flow);
      expect(container.textContent).not.toMatch(/[–—]| - /);
      unmount();
    }
  });
});
