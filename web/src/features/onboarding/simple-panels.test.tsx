import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it } from "vitest";

import { EMPTY } from "@/features/profile/api";

import { StepLooking, StepPay, StepWhere, StepWork } from "./step-panels";
import { draftOf, type Draft } from "./steps";

beforeEach(() => {
  window.localStorage.clear();
});

describe("simple wizard panels (O10)", () => {
  it("starts with roles and keeps every targeting choice under Advanced", async () => {
    render(<StepLooking draft={draftOf(EMPTY)} change={() => {}} />);
    expect(screen.getAllByRole("textbox")).toHaveLength(1);
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Advanced" }));
    expect(screen.getAllByRole("checkbox")).toHaveLength(13);
    expect(screen.getByLabelText("Industries (optional)")).toBeVisible();
  });

  it("keeps saved role patterns under Advanced with access to Search options", async () => {
    render(
      <MemoryRouter>
        <StepLooking
          draft={{ ...draftOf(EMPTY), roles: "^data.*engineer$" }}
          patterns={{ roles: true, places: false }}
          change={() => {}}
        />
      </MemoryRouter>,
    );
    expect(screen.queryByRole("textbox")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Advanced" }));
    expect(screen.getByLabelText("Roles")).toHaveValue("^data.*engineer$");
    expect(screen.getByLabelText("Roles")).toHaveAttribute("readonly");
    expect(
      screen.getByRole("link", { name: "Search options" }),
    ).toHaveAttribute("href", "/profile?tab=search");
  });

  it("puts every contract in one group whose last box is Freelance mission", () => {
    render(<StepWork draft={draftOf(EMPTY)} change={() => {}} />);
    const cards = screen.getAllByRole("group");
    expect(cards).toHaveLength(1);
    expect(cards[0]).toHaveAccessibleName("Contracts (choose any)");
    expect(
      within(cards[0]!)
        .getAllByRole("checkbox")
        .map((box) => box.parentElement?.textContent),
    ).toEqual([
      "Permanent",
      "Fixed term",
      "VIE",
      "Internship",
      "Apprenticeship",
      "Freelance mission",
    ]);
    expect(screen.queryByText("Freelance work")).toBeNull();
  });

  it("saves the same codes as before, and keeps a code it does not offer", async () => {
    function Work() {
      const [draft, setDraft] = useState<Draft>({
        ...draftOf(EMPTY),
        contracts: ["Seasonal", "CDI"],
      });
      return (
        <>
          <StepWork
            draft={draft}
            change={(patch) => {
              setDraft((old) => ({ ...old, ...patch }));
            }}
          />
          <output data-testid="saved">{draft.contracts.join(",")}</output>
        </>
      );
    }
    render(<Work />);
    expect(screen.getByRole("checkbox", { name: "Permanent" })).toBeChecked();
    await userEvent.click(
      screen.getByRole("checkbox", { name: "Freelance mission" }),
    );
    await userEvent.click(screen.getByRole("checkbox", { name: "VIE" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Permanent" }));
    expect(screen.getByTestId("saved")).toHaveTextContent(
      "Seasonal,Freelance,VIE",
    );
  });

  it("ticks a box from anywhere on its card, once per click, and marks the card", async () => {
    function Work() {
      const [draft, setDraft] = useState<Draft>(draftOf(EMPTY));
      return (
        <>
          <StepWork
            draft={draft}
            change={(patch) => {
              setDraft((old) => ({ ...old, ...patch }));
            }}
          />
          <output data-testid="saved">{draft.contracts.join(",")}</output>
        </>
      );
    }
    render(<Work />);
    const box = screen.getByRole("checkbox", { name: "VIE" });
    const card = box.parentElement!;
    expect(card.tagName).toBe("LABEL");
    await userEvent.click(within(card).getByText("VIE"));
    expect(box).toBeChecked();
    expect(screen.getByTestId("saved")).toHaveTextContent(/^VIE$/);
    // The card is marked while its box is ticked.
    expect(card).toHaveClass("has-data-[state=checked]:border-primary");
    expect(box).toHaveAttribute("data-state", "checked");
    await userEvent.click(box);
    expect(box).not.toBeChecked();
    expect(screen.getByTestId("saved")).toBeEmptyDOMElement();
    // The keyboard ticks it too.
    box.focus();
    await userEvent.keyboard(" ");
    expect(box).toBeChecked();
  });

  it("chooses one remote stance by its card or the arrow keys", async () => {
    function Where() {
      const [draft, setDraft] = useState<Draft>(draftOf(EMPTY));
      return (
        <MemoryRouter>
          <StepWhere
            draft={draft}
            change={(patch) => {
              setDraft((old) => ({ ...old, ...patch }));
            }}
          />
          <output data-testid="saved">{draft.remote ?? "none"}</output>
        </MemoryRouter>
      );
    }
    render(<Where />);
    const stances = screen.getByRole("group", {
      name: "Remote, hybrid or on site",
    });
    expect(within(stances).getAllByRole("radio")).toHaveLength(4);
    expect(screen.getByTestId("saved")).toHaveTextContent("none");
    await userEvent.click(within(stances).getByText("Hybrid"));
    expect(
      within(stances).getByRole("radio", { name: "Hybrid" }),
    ).toBeChecked();
    expect(screen.getByTestId("saved")).toHaveTextContent("hybrid");
    // Held, as a person presses it: the next stance takes the focus and the choice.
    await userEvent.keyboard("{ArrowDown>}");
    await userEvent.keyboard("{/ArrowDown}");
    const office = within(stances).getByRole("radio", { name: "On site" });
    expect(office).toHaveFocus();
    expect(office).toBeChecked();
    expect(screen.getByTestId("saved")).toHaveTextContent("office");
    expect(
      within(stances).getByRole("radio", { name: "Hybrid" }),
    ).not.toBeChecked();
    // Anywhere, a box alone on its row, ticks from its words.
    await userEvent.click(screen.getByText("Anywhere"));
    expect(screen.getByRole("checkbox", { name: "Anywhere" })).toBeChecked();
  });

  it("folds pay figures without losing a typed value and restores the open preference", async () => {
    function Pay() {
      const [draft, setDraft] = useState<Draft>(draftOf(EMPTY));
      return (
        <StepPay
          draft={draft}
          change={(patch) => {
            setDraft((old) => ({ ...old, ...patch }));
          }}
        />
      );
    }
    const first = render(<Pay />);
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    const advanced = screen.getByRole("button", { name: "Advanced" });
    await userEvent.click(advanced);
    await userEvent.type(
      within(
        screen.getByRole("group", { name: "Yearly salary" }),
      ).getByLabelText("From"),
      "45000",
    );
    await userEvent.click(advanced);
    await userEvent.click(advanced);
    expect(
      within(
        screen.getByRole("group", { name: "Yearly salary" }),
      ).getByLabelText("From"),
    ).toHaveValue("45000");
    first.unmount();
    render(<Pay />);
    expect(screen.getByRole("button", { name: "Advanced" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });
});
