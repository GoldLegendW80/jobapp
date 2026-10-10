import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  BOTTOM_SLACK,
  useStickToBottom,
  wasAtBottom,
} from "./use-stick-to-bottom";

describe("wasAtBottom", () => {
  // A list 400 high whose content was 1000 high and has grown to 1200.
  const box = (scrollTop: number, scrollHeight = 1200) => ({
    scrollTop,
    scrollHeight,
    clientHeight: 400,
  });

  it("is true for a new list", () => {
    expect(wasAtBottom(box(0), null)).toBe(true);
  });

  it("is true at the earlier bottom and within the slack", () => {
    expect(wasAtBottom(box(600), 1000)).toBe(true);
    expect(wasAtBottom(box(600 - BOTTOM_SLACK), 1000)).toBe(true);
    // Browsers report fractions and overscroll.
    expect(wasAtBottom(box(599.5), 1000)).toBe(true);
    expect(wasAtBottom(box(620), 1000)).toBe(true);
  });

  it("is false once the reader scrolled up past the slack", () => {
    expect(wasAtBottom(box(600 - BOTTOM_SLACK - 1), 1000)).toBe(false);
    expect(wasAtBottom(box(0), 1000)).toBe(false);
  });

  it("judges a list that got shorter against its height now", () => {
    // Content went from 1000 to 700: the browser moved the view up to 300, its new bottom.
    expect(wasAtBottom(box(300, 700), 1000)).toBe(true);
    expect(wasAtBottom(box(100, 700), 1000)).toBe(false);
  });

  it("is true for a list shorter than its box", () => {
    expect(
      wasAtBottom({ scrollTop: 0, scrollHeight: 120, clientHeight: 400 }, 100),
    ).toBe(true);
  });
});

describe("useStickToBottom", () => {
  function list(scrollHeight: number) {
    const node = { scrollTop: 0, scrollHeight, clientHeight: 400 };
    const ref = { current: node as unknown as HTMLDivElement };
    return { node, ref };
  }

  it("follows the content while the reader is at the bottom, and only then", () => {
    const { node, ref } = list(1000);
    const { result } = renderHook(() => useStickToBottom(ref));
    result.current.follow(); // a new list opens on its newest content
    expect(node.scrollTop).toBe(1000);
    node.scrollHeight = 1100;
    result.current.follow();
    expect(node.scrollTop).toBe(1100);
    // The reader scrolls up, and the content grows twice: the view stays.
    node.scrollTop = 150;
    node.scrollHeight = 1200;
    result.current.follow();
    node.scrollHeight = 1300;
    result.current.follow();
    expect(node.scrollTop).toBe(150);
    // The reader comes back to the bottom, a few pixels short: it follows again.
    node.scrollTop = 1300 - 400 - 5;
    node.scrollHeight = 1400;
    result.current.follow();
    expect(node.scrollTop).toBe(1400);
  });

  it("goes to the bottom when forced, from anywhere", () => {
    const { node, ref } = list(1000);
    const { result } = renderHook(() => useStickToBottom(ref));
    result.current.follow();
    node.scrollTop = 0;
    node.scrollHeight = 1200;
    result.current.follow(true);
    expect(node.scrollTop).toBe(1200);
  });

  it("does nothing without a list", () => {
    const { result } = renderHook(() => useStickToBottom({ current: null }));
    expect(() => {
      result.current.follow();
    }).not.toThrow();
  });
});
