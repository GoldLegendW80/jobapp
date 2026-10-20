import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AdvancedSection } from "./advanced-section";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.restoreAllMocks();
});

const toggle = (name = "Advanced") => screen.getByRole("button", { name });

describe("AdvancedSection", () => {
  it("starts collapsed and renders no children", () => {
    render(
      <AdvancedSection pageKey="p">
        <input aria-label="Ceiling" />
      </AdvancedSection>,
    );
    expect(toggle()).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByLabelText("Ceiling")).toBeNull();
    expect(toggle().getAttribute("aria-controls")).toBe(
      document.querySelector("[role=region]")?.id,
    );
  });

  it("opens and closes from the keyboard", async () => {
    const user = userEvent.setup();
    render(
      <AdvancedSection>
        <input aria-label="Ceiling" />
      </AdvancedSection>,
    );
    await user.tab();
    expect(toggle()).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(toggle()).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByLabelText("Ceiling")).toBeVisible();
    await user.keyboard(" ");
    expect(toggle()).toHaveAttribute("aria-expanded", "false");
    expect(toggle()).toHaveFocus();
  });

  it("keeps the state per page key across a remount", () => {
    const first = render(
      <AdvancedSection pageKey="search">
        <p>a</p>
      </AdvancedSection>,
    );
    fireEvent.click(toggle());
    first.unmount();
    render(
      <AdvancedSection pageKey="search">
        <p>a</p>
      </AdvancedSection>,
    );
    expect(toggle()).toHaveAttribute("aria-expanded", "true");
  });

  it("keeps two sections of one page apart", () => {
    render(
      <>
        <AdvancedSection pageKey="one" title="First">
          <p>first body</p>
        </AdvancedSection>
        <AdvancedSection pageKey="two" title="Second">
          <p>second body</p>
        </AdvancedSection>
      </>,
    );
    fireEvent.click(toggle("First"));
    expect(screen.getByText("first body")).toBeVisible();
    expect(screen.queryByText("second body")).toBeNull();
    expect(toggle("Second")).toHaveAttribute("aria-expanded", "false");
    expect(window.localStorage.getItem("advanced:two")).toBeNull();
  });

  it("starts collapsed and throws nothing when storage throws on read and write", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    render(
      <AdvancedSection pageKey="p">
        <p>body</p>
      </AdvancedSection>,
    );
    expect(toggle()).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle());
    expect(toggle()).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("body")).toBeVisible();
  });

  it("opens and focuses a field with an error", () => {
    const { rerender } = render(
      <AdvancedSection pageKey="p">
        <input id="ceiling" aria-label="Ceiling" />
      </AdvancedSection>,
    );
    expect(screen.queryByLabelText("Ceiling")).toBeNull();
    rerender(
      <AdvancedSection pageKey="p" errorFieldId="ceiling">
        <input id="ceiling" aria-label="Ceiling" />
      </AdvancedSection>,
    );
    expect(toggle()).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByLabelText("Ceiling")).toHaveFocus();
  });

  it("keeps children mounted while folded when asked", () => {
    render(
      <AdvancedSection keepMounted>
        <input aria-label="Ceiling" />
      </AdvancedSection>,
    );
    expect(toggle()).toHaveAttribute("aria-expanded", "false");
    expect(
      screen.getByLabelText("Ceiling", { selector: "input" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByLabelText("Ceiling", { selector: "input" }),
    ).not.toBeVisible();
  });

  it("can be held from outside", () => {
    const onOpenChange = vi.fn();
    render(
      <AdvancedSection open={false} onOpenChange={onOpenChange}>
        <p>body</p>
      </AdvancedSection>,
    );
    fireEvent.click(toggle());
    expect(onOpenChange).toHaveBeenCalledWith(true);
    expect(toggle()).toHaveAttribute("aria-expanded", "false");
  });

  it("has a touch sized button at phone width", () => {
    render(
      <AdvancedSection>
        <p>body</p>
      </AdvancedSection>,
    );
    expect(toggle().className).toContain("min-h-11");
  });
});
