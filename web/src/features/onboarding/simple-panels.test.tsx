import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it } from "vitest";

import { EMPTY } from "@/features/profile/api";

import { StepLooking, StepPay, StepWork } from "./step-panels";
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

  it("gives employment and freelance separate cards with at most five choices each", () => {
    render(<StepWork draft={draftOf(EMPTY)} change={() => {}} />);
    const cards = screen.getAllByRole("group");
    expect(cards).toHaveLength(2);
    expect(
      cards.map((card) => within(card).getAllByRole("checkbox").length),
    ).toEqual([5, 1]);
    expect(screen.getAllByRole("checkbox")).toHaveLength(6);
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
