import type { Sweep, SweepSource } from "@/features/jobs/api/sweeps-api";

/** The job websites of Find jobs, as the sweep names them (api interactive.py). */
const WEBSITES: Record<string, { kind: string; host: string }> = {
  "Welcome to the Jungle": {
    kind: "welcome_to_the_jungle",
    host: "www.welcometothejungle.com",
  },
  "Free-Work": { kind: "freework", host: "www.free-work.com" },
  Indeed: { kind: "indeed", host: "fr.indeed.com" },
  Arbeitnow: { kind: "arbeitnow", host: "www.arbeitnow.com" },
};

/** A job website of a running sweep, searched in the person's browser by the assistant. */
export function websiteOfSweep(name: string, phase: string): SweepSource {
  const website = WEBSITES[name] ?? { kind: "custom", host: name };
  return {
    source_id: `source-${website.kind}`,
    mode: "llm_browser",
    phase,
    kind: website.kind,
    query: "",
    location: null,
    host: website.host,
    result_pages: 0,
    found: phase === "done" ? 6 : 0,
    new: phase === "done" ? 2 : 0,
    skipped_by_rule: 0,
    items_skipped: 0,
    error: null,
  };
}

/** A sweep under way, reading `sources`. */
export function runningSweep(sources: SweepSource[]): Sweep {
  const done = sources.filter((source) => source.phase === "done");
  return {
    id: "5f1e2d3c-4b5a-4968-8776-655443322110",
    trigger: "manual",
    status: "running",
    started_at: "2026-11-06T09:00:00Z",
    finished_at: null,
    error: null,
    boards_total: 0,
    boards_done: 0,
    boards_ok: 0,
    boards_failed: [],
    current_board:
      sources.find((source) => source.phase === "running")?.source_id ?? null,
    seen: done.length * 40,
    matched: done.length * 6,
    new: done.reduce((sum, source) => sum + source.new, 0),
    duplicates: 0,
    skipped_by_rule: 0,
    duration: null,
    sources,
  };
}
