import { describe, expect, it } from "vitest";

import {
  applyPartial,
  applyStep,
  emptyStream,
  lastText,
  type StreamState,
  type TextPart,
} from "./live-text";
import type { RunStep } from "./runs-api";

function step(
  seq: number,
  kind: string,
  data: Record<string, unknown> = {},
): RunStep {
  return { seq, at: "2026-10-10T08:00:00Z", kind, data };
}

function part(
  block: number,
  after: number,
  offset: number,
  text: string,
): TextPart {
  return { block, after, offset, text };
}

function feed(
  state: StreamState,
  events: Array<RunStep | TextPart>,
): StreamState {
  return events.reduce<StreamState>(
    (current, event) =>
      "seq" in event ? applyStep(current, event) : applyPartial(current, event),
    state,
  );
}

// Accents, a combining mark, CJK, an emoji outside the BMP and a joined emoji sequence.
const UNICODE = "café déjà vu, été, 日本語, 😀 and 👩‍💻 done";

describe("live text", () => {
  it("grows piece by piece and is replaced by the stored text", () => {
    let state = feed(emptyStream, [
      step(1, "status", { status: "queued" }),
      step(2, "status", { status: "running" }),
      part(1, 2, 0, "It fi"),
    ]);
    expect(state.live?.text).toBe("It fi");
    state = applyPartial(state, part(1, 2, 5, "ts well"));
    expect(state.live).toEqual({ block: 1, after: 2, text: "It fits well" });
    // The stored step takes its place in the same update: both are never there together.
    state = applyStep(state, step(3, "text", { text: "It fits well." }));
    expect(state.live).toBeNull();
    expect(lastText(state.steps)).toBe("It fits well.");
  });

  it("applies the same piece twice, or an older one, without changing the text", () => {
    const first = part(1, 0, 0, "abc");
    const second = part(1, 0, 3, "def");
    const state = feed(emptyStream, [first, second]);
    expect(state.live?.text).toBe("abcdef");
    expect(feed(state, [second]).live?.text).toBe("abcdef");
    expect(feed(state, [first]).live?.text).toBe("abcdef");
    expect(feed(state, [first, second, first]).live?.text).toBe("abcdef");
    // Nothing changed: the same object, so nothing renders again.
    expect(feed(state, [first])).toBe(state);
  });

  it("takes a whole snapshot from offset 0 after a reconnect", () => {
    const state = feed(emptyStream, [part(1, 0, 0, "abc")]);
    expect(applyPartial(state, part(1, 0, 0, "abcdefgh")).live?.text).toBe(
      "abcdefgh",
    );
  });

  it("ignores a piece that leaves a gap", () => {
    const state = feed(emptyStream, [part(1, 0, 0, "abc")]);
    expect(applyPartial(state, part(1, 0, 5, "xyz"))).toBe(state);
    // Also for a block not seen yet: it must start at its beginning.
    expect(applyPartial(emptyStream, part(1, 0, 2, "xyz"))).toBe(emptyStream);
    expect(applyPartial(state, part(2, 0, 2, "xyz"))).toBe(state);
  });

  it("starts over with a newer block and ignores an older one", () => {
    let state = feed(emptyStream, [part(1, 0, 0, "first block")]);
    state = applyPartial(state, part(2, 0, 0, ""));
    expect(state.live).toEqual({ block: 2, after: 0, text: "" });
    state = applyPartial(state, part(2, 0, 0, "second"));
    expect(state.live?.text).toBe("second");
    expect(applyPartial(state, part(1, 0, 11, " late"))).toBe(state);
  });

  it("ignores a delta that arrives after the complete message", () => {
    const state = feed(emptyStream, [
      step(1, "status", { status: "running" }),
      part(1, 1, 0, "The ans"),
      step(2, "text", { text: "The answer." }),
    ]);
    expect(state.live).toBeNull();
    expect(applyPartial(state, part(1, 1, 7, "wer."))).toBe(state);
    expect(applyPartial(state, part(1, 1, 0, "The answer."))).toBe(state);
    // The next block, begun after that text, is live again.
    expect(applyPartial(state, part(2, 2, 0, "Next")).live?.text).toBe("Next");
  });

  it("keeps the live text through steps that are not its text", () => {
    const state = feed(emptyStream, [
      step(1, "status", { status: "running" }),
      step(2, "text", { text: "An earlier text." }),
      part(1, 2, 0, "Being written"),
      step(3, "rate_limit", { status: "allowed" }),
      step(4, "tool_denied", { name: "Bash" }),
    ]);
    expect(state.live?.text).toBe("Being written");
    expect(state.steps).toHaveLength(4);
  });

  it.each(["cancelled", "failed", "done"])(
    "drops the live text when the run ends %s without a text step for it",
    (status) => {
      const state = feed(emptyStream, [
        step(1, "status", { status: "running" }),
        part(1, 1, 0, "Half an ans"),
        step(2, "status", { status }),
      ]);
      expect(state.live).toBeNull();
      expect(lastText(state.steps)).toBeUndefined();
      // And nothing is live after the end, whatever still arrives.
      expect(applyPartial(state, part(1, 1, 11, "wer"))).toBe(state);
      expect(applyPartial(state, part(2, 2, 0, "new"))).toBe(state);
    },
  );

  it("never takes a step twice", () => {
    const state = feed(emptyStream, [step(1, "status"), step(2, "text")]);
    expect(applyStep(state, step(2, "text"))).toBe(state);
    expect(applyStep(state, step(1, "status"))).toBe(state);
    expect(applyStep(state, step(3, "usage")).steps.map((s) => s.seq)).toEqual([
      1, 2, 3,
    ]);
  });

  it("counts offsets in UTF-16 units, as the server does", () => {
    // "😀" is two units: the piece after it starts at offset 2.
    const state = feed(emptyStream, [
      part(1, 0, 0, "😀"),
      part(1, 0, 2, "é"),
      part(1, 0, 3, "👩"),
      part(1, 0, 5, "‍💻"),
    ]);
    expect(state.live?.text).toBe("😀é👩‍💻");
  });

  it("reads the newest stored text", () => {
    expect(lastText([])).toBeUndefined();
    expect(
      lastText([
        step(1, "text", { text: "one" }),
        step(2, "tool_call", { name: "x" }),
        step(3, "text", { text: "two" }),
        step(4, "text", { text: 5 }),
        step(5, "usage"),
      ]),
    ).toBe("two");
  });
});

/**
 * The server as live.py and sse.py define it, for one run answering in two text blocks: what one
 * connection sends from a given moment on. `log` is everything that happens, in order; a
 * connection opened at `from` with the client's last seq replays the steps it misses, then goes
 * on, and for the block being written it starts at offset 0 with everything written so far.
 */
type Happening =
  | { type: "step"; step: RunStep }
  | { type: "open"; block: number; after: number }
  | { type: "append"; block: number; text: string }
  | { type: "close" };

function codePoints(text: string, size: number): string[] {
  const points = Array.from(text);
  const out: string[] = [];
  for (let i = 0; i < points.length; i += size) {
    out.push(points.slice(i, i + size).join(""));
  }
  return out;
}

function runLog(
  first: string,
  second: string,
  size: number,
  ending: string,
): Happening[] {
  const log: Happening[] = [];
  let seq = 0;
  const add = (kind: string, data: Record<string, unknown> = {}) => {
    seq += 1;
    log.push({ type: "step", step: step(seq, kind, data) });
  };
  const block = (number: number, text: string, complete: boolean) => {
    log.push({ type: "open", block: number, after: seq });
    for (const piece of codePoints(text, size)) {
      log.push({ type: "append", block: number, text: piece });
    }
    if (complete) {
      add("text", { text });
      log.push({ type: "close" });
    }
  };
  add("status", { status: "queued" });
  add("status", { status: "running" });
  add("thinking");
  block(1, first, true);
  add("tool_call", { name: "get_job" });
  add("rate_limit");
  add("tool_result");
  block(2, second, ending === "done");
  if (ending === "done") add("usage");
  add("status", { status: ending });
  log.push({ type: "close" });
  return log;
}

/**
 * What a connection opened when `from` happenings were over sends, for a client at `last`: each
 * event with how much of the log had happened when it was sent.
 */
function* connection(
  log: Happening[],
  from: number,
  last: number,
): Generator<[RunStep | TextPart, number]> {
  // The state of the world at the moment of the connection.
  let open: { block: number; after: number; text: string } | null = null;
  for (const item of log.slice(0, from)) {
    if (item.type === "step" && item.step.seq > last) yield [item.step, from];
    if (item.type === "open") open = { ...item, text: "" };
    if (item.type === "append" && open) open.text += item.text;
    if (item.type === "close") open = null;
  }
  let sent = 0;
  if (open !== null) {
    yield [part(open.block, open.after, 0, open.text), from];
    sent = open.text.length;
  }
  for (const [index, item] of log.slice(from).entries()) {
    const at = from + index + 1;
    if (item.type === "step") yield [item.step, at];
    if (item.type === "open") {
      open = { ...item, text: "" };
      sent = 0;
      yield [part(item.block, item.after, 0, ""), at];
    }
    if (item.type === "append" && open) {
      open.text += item.text;
      yield [part(open.block, open.after, sent, item.text), at];
      sent = open.text.length;
    }
    if (item.type === "close") open = null;
  }
}

function truth(log: Happening[]): Map<number, string> {
  const texts = new Map<number, string>();
  for (const item of log) {
    if (item.type === "append") {
      texts.set(item.block, (texts.get(item.block) ?? "") + item.text);
    }
  }
  return texts;
}

/** Follow the run, cutting the connection after each of `cuts` events and reconnecting. */
function follow(log: Happening[], cuts: number[], away: number): StreamState {
  const texts = truth(log);
  let state = emptyStream;
  let position = 0; // how much of the log has happened
  for (const cut of [...cuts, Infinity]) {
    let taken = 0;
    let finished = true;
    const last = state.steps.at(-1)?.seq ?? 0;
    for (const [event, at] of connection(log, position, last)) {
      state =
        "seq" in event ? applyStep(state, event) : applyPartial(state, event);
      // At every moment: steps once and in order, and the live text a beginning of its block.
      expect(state.steps.map((s) => s.seq)).toEqual(
        state.steps.map((_, index) => index + 1),
      );
      if (state.live) {
        expect(texts.get(state.live.block)?.startsWith(state.live.text)).toBe(
          true,
        );
      }
      taken += 1;
      if (taken >= cut) {
        // The run moves on by `away` happenings while the client is gone.
        position = Math.min(log.length, at + away);
        finished = false;
        break;
      }
    }
    if (finished) break;
  }
  return state;
}

describe("reconnecting at any moment", () => {
  const first = `Let me read: ${UNICODE}`;
  const second = UNICODE.repeat(3);

  it.each([0, 1, 4, 1000])(
    "ends with every step once and the full text once, the run moving on by %i while away",
    (away) => {
      const log = runLog(first, second, 3, "done");
      const steps = log.filter((h) => h.type === "step").length;
      const total = log.length + 4;
      for (let cut = 1; cut <= total; cut += 1) {
        const state = follow(log, [cut], away);
        expect(state.steps.map((s) => s.seq)).toEqual(
          Array.from({ length: steps }, (_, index) => index + 1),
        );
        expect(
          state.steps.filter((s) => s.kind === "text").map((s) => s.data.text),
        ).toEqual([first, second]);
        expect(state.live).toBeNull();
      }
    },
  );

  it("holds for many cuts at pseudo random moments", () => {
    let seed = 20261010;
    const random = (max: number) => {
      seed = (seed * 1103515245 + 12345) % 2147483648;
      return seed % max;
    };
    for (let round = 0; round < 200; round += 1) {
      const log = runLog(
        first.slice(0, random(first.length) + 1),
        second,
        random(6) + 1,
        "done",
      );
      const cuts = Array.from({ length: random(40) + 1 }, () => random(7) + 1);
      const state = follow(log, cuts, random(4));
      expect(state.live).toBeNull();
      expect(lastText(state.steps)).toBe(second);
      expect(state.steps).toHaveLength(
        log.filter((h) => h.type === "step").length,
      );
    }
  });

  it.each(["cancelled", "failed"])(
    "shows the unfinished answer until the run is %s, then nothing, whatever the cut",
    (ending) => {
      const log = runLog(first, second, 4, ending);
      let seen = false;
      for (let cut = 1; cut <= log.length + 4; cut += 1) {
        const texts = truth(log);
        let state = emptyStream;
        for (const [event] of connection(log, 0, 0)) {
          state =
            "seq" in event
              ? applyStep(state, event)
              : applyPartial(state, event);
          if (state.live?.block === 2 && state.live.text) seen = true;
        }
        expect(texts.get(2)).toBe(second);
        const cutState = follow(log, [cut], 1);
        expect(cutState.live).toBeNull();
        expect(lastText(cutState.steps)).toBe(first);
      }
      expect(seen).toBe(true);
    },
  );
});
