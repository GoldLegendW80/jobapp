import { screen, within } from "@testing-library/react";

/** A link of the page header's breadcrumb, by name: the app's navigation has links of the same names. */
export function crumb(name: string): HTMLElement {
  return within(
    screen.getByRole("navigation", { name: "Breadcrumb" }),
  ).getByRole("link", { name });
}
