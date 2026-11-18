/** Jobs J21: a web search in an agent's step log, "Web search: <query>" and its result count. */
import { describe, expect, it } from "vitest";

import type { RunStep } from "../api/runs-api";
import { describeStep } from "../helpers/run-steps";

const step = (kind: string, data: Record<string, unknown>): RunStep => ({
  seq: 1,
  at: "2026-11-18T10:00:00Z",
  kind,
  data,
});

describe("a web search in the step log", () => {
  it("titles a private run's search with its stored query", () => {
    const view = describeStep(
      step("tool_call", {
        id: "s1",
        name: "WebSearch",
        input_size: 40,
        preview: { query: "data scientist CDI La Reunion" },
      }),
      null,
    );
    expect(view?.title).toBe("Web search: data scientist CDI La Reunion");
    expect(view?.body).toBeUndefined();
  });

  it("titles a public run's search with its argument", () => {
    const view = describeStep(
      step("tool_call", {
        id: "s1",
        name: "WebSearch",
        input: { query: "ingenieur IA Reunion" },
      }),
      null,
    );
    expect(view?.title).toBe("Web search: ingenieur IA Reunion");
  });

  it("keeps the usual title when no query was kept", () => {
    const view = describeStep(
      step("tool_call", { id: "s1", name: "WebSearch", input_size: 12 }),
      null,
    );
    expect(view?.title).toBe("Tool call: WebSearch");
  });

  it("says how many results the answer listed, and an error as an error", () => {
    expect(
      describeStep(
        step("tool_result", {
          id: "s1",
          is_error: false,
          size: 900,
          blocks: 0,
          results: 2,
        }),
        null,
      )?.title,
    ).toBe("2 results");
    expect(
      describeStep(
        step("tool_result", {
          id: "s1",
          is_error: false,
          size: 30,
          blocks: 0,
          results: 1,
        }),
        null,
      )?.title,
    ).toBe("1 result");
    const failed = describeStep(
      step("tool_result", {
        id: "s1",
        is_error: true,
        size: 30,
        blocks: 0,
        results: 0,
      }),
      null,
    );
    expect(failed?.title).toBe("Tool error");
    expect(failed?.tone).toBe("error");
  });
});
