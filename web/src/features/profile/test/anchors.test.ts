import { describe, expect, it } from "vitest";

import { PROFILE_TABS, tabOf } from "../helpers/anchors";

describe("tabOf", () => {
  it.each(PROFILE_TABS.map((tab) => [tab.path, tab.key]))(
    "reads %s as %s",
    (path, key) => {
      expect(tabOf(path)).toBe(key);
      expect(tabOf(`${path}/`)).toBe(key);
      expect(tabOf(`${path}//`)).toBe(key);
    },
  );

  it.each([
    "/",
    "",
    "/profile/contacts",
    "/profile/contact/x",
    "/profile/Contact",
    "/profiles",
  ])("reads %j as the CVs", (path) => {
    expect(tabOf(path)).toBe("cvs");
  });
});
