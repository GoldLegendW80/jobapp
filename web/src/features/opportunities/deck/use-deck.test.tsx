/// <reference types="node" />
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createQueryClient } from "@/lib/query";

import { NO_FILTERS } from "../opportunities-api";
import { fakeDeck, makeCard } from "./deck-test-api";
import { CANCELLABLE, useDeck } from "./use-deck";

function renderDeck() {
  const client = createQueryClient();
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return renderHook(() => useDeck(NO_FILTERS), { wrapper });
}

const [one, two, three] = [makeCard(1), makeCard(2), makeCard(3)];

beforeEach(() => {
  vi.restoreAllMocks();
  toast.dismiss();
});

describe("a handler kept from an earlier render", () => {
  it("sends no Like for the card a Pass just took away", async () => {
    const deck = fakeDeck([one, two, three]);
    const { result } = renderDeck();
    await waitFor(() => {
      expect(result.current.top?.id).toBe(one.id);
    });
    // What a key handler or a drag release still holds before the next commit.
    const kept = result.current.swipe;
    act(() => {
      result.current.swipe("pass");
    });
    await waitFor(() => {
      expect(deck.calls).toContain(`POST pass ${one.id}`);
    });
    await waitFor(() => {
      expect(result.current.busy).toBe(false);
    });
    act(() => {
      kept("like");
    });
    await waitFor(() => {
      expect(result.current.busy).toBe(false);
    });
    // The kept handler acts on the card shown now, never on the passed one.
    expect(deck.calls).not.toContain(`POST like ${one.id}`);
    expect(deck.calls).toContain(`POST like ${two.id}`);
  });

  it("undoes no swipe twice", async () => {
    const deck = fakeDeck([one, two, three]);
    const { result } = renderDeck();
    await waitFor(() => {
      expect(result.current.top?.id).toBe(one.id);
    });
    act(() => {
      result.current.swipe("pass");
    });
    await waitFor(() => {
      expect(result.current.last?.card.id).toBe(one.id);
    });
    const kept = result.current.undo;
    act(() => {
      result.current.undo();
    });
    await waitFor(() => {
      expect(result.current.last).toBeNull();
    });
    await act(async () => {
      kept();
      await Promise.resolve();
    });
    expect(
      deck.calls.filter((c) => c === `DELETE pass ${one.id}`),
    ).toHaveLength(1);
    expect(result.current.top?.id).toBe(one.id);
  });
});

describe("the Undo of a toast", () => {
  it("acts only on its own swipe, and the older toast is dismissed", async () => {
    const offered: { id: string; onClick: () => void }[] = [];
    let n = 0;
    vi.spyOn(toast, "success").mockImplementation((_message, options) => {
      const id = `t${String(++n)}`;
      const action = options?.action as { onClick: () => void } | undefined;
      if (action) offered.push({ id, onClick: action.onClick });
      return id;
    });
    const dismiss = vi.spyOn(toast, "dismiss");
    const deck = fakeDeck([one, two, three]);
    const { result } = renderDeck();
    await waitFor(() => {
      expect(result.current.top?.id).toBe(one.id);
    });
    act(() => {
      result.current.swipe("pass");
    });
    await waitFor(() => {
      expect(result.current.top?.id).toBe(two.id);
    });
    act(() => {
      result.current.swipe("like");
    });
    await waitFor(() => {
      expect(result.current.last?.card.id).toBe(two.id);
    });
    expect(offered.map((o) => o.id)).toEqual(["t1", "t2"]);
    expect(dismiss).toHaveBeenCalledWith("t1");

    // The "Passed." toast's Undo, clicked after the Like: nothing is sent.
    await act(async () => {
      offered[0]?.onClick();
      await Promise.resolve();
    });
    expect(result.current.busy).toBe(false);
    expect(deck.calls).not.toContain(`DELETE pass ${one.id}`);
    expect(deck.calls.some((c) => c.startsWith("POST cancel"))).toBe(false);
    expect(deck.calls.some((c) => c.startsWith("GET application"))).toBe(false);

    // Its own toast's Undo cancels the Like and dismisses that toast.
    act(() => {
      offered[1]?.onClick();
    });
    await waitFor(() => {
      expect(deck.calls.some((c) => c.startsWith("POST cancel"))).toBe(true);
    });
    expect(dismiss).toHaveBeenCalledWith("t2");
  });
});

describe("CANCELLABLE", () => {
  it("is the set apply's cancel accepts (api/app/apply/gate.py)", () => {
    const gate = readFileSync(
      path.resolve(
        path.dirname(fileURLToPath(import.meta.url)),
        "../../../../../api/app/apply/gate.py",
      ),
      "utf8",
    );
    const block = /^CANCELLABLE = frozenset\(\s*\{([^}]*)\}/m.exec(gate);
    expect(block).not.toBeNull();
    const states = [...(block?.[1] ?? "").matchAll(/State\.(\w+)/g)].map(
      (m) => m[1],
    );
    expect(states.length).toBeGreaterThan(0);
    expect([...CANCELLABLE].sort()).toEqual(states.sort());
  });
});
