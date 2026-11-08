/**
 * Follow ups for stories and tests (SPEC-apply.md, "Follow ups"): invented postings at invented
 * companies, and a fake of /api/apply/followups that keeps its list in memory, so Write a draft,
 * Save and "I sent it" change what the page shows next.
 */
import { http, HttpResponse } from "msw";

import type { FollowUp, FollowUps } from "@/features/apply/api/followups-api";
import { server } from "@/test/server";

export const TODAY = "2026-11-07";

export function followUp(overrides: Partial<FollowUp> = {}): FollowUp {
  return {
    posting_id: "posting-fu-1",
    company: "Example Robotics",
    title: "Data Engineer",
    status: "applied",
    applied_on: "2026-10-28",
    applied_from: "posting",
    kind: "follow_up",
    urgency: "due",
    next_on: "2026-11-04",
    count: 0,
    sent: [],
    can_draft: true,
    draft: null,
    ...overrides,
  };
}

export const DRAFT_BODY =
  "Hello,\n\nI applied for the Data Engineer role on 28 October. Since then I shipped the nightly pipeline I described in my letter.\n\nWould a short call this week or next suit you?\n\nAlex Example";

export const written = (overrides: Partial<FollowUp> = {}) =>
  followUp({
    draft: {
      subject: "Data Engineer: following up on my application",
      body: DRAFT_BODY,
      issues: [],
      written_by: "agent",
      writing: false,
      error: null,
      mail_draft_id: null,
    },
    ...overrides,
  });

export const waiting = () =>
  followUp({
    posting_id: "posting-fu-2",
    company: "Sample Logistics",
    title: "Platform Engineer",
    applied_on: "2026-11-03",
    urgency: "waiting",
    next_on: "2026-11-10",
  });

export const cold = () =>
  followUp({
    posting_id: "posting-fu-3",
    company: "Demo Analytics",
    title: "Analytics Engineer",
    applied_on: "2026-09-20",
    urgency: "cold",
    next_on: null,
    count: 2,
    sent: ["2026-09-28", "2026-10-06"],
    can_draft: false,
  });

/** The fake api of the follow ups, its list kept here; answers what a click changes. */
export function fakeFollowUps(start: FollowUp[], enabled = true) {
  let list: FollowUps = { enabled, today: TODAY, items: start };
  const find = (id: unknown) =>
    list.items.find((item) => item.posting_id === String(id));
  const put = (item: FollowUp) => {
    list = {
      ...list,
      items: list.items.map((one) =>
        one.posting_id === item.posting_id ? item : one,
      ),
    };
    return HttpResponse.json(item);
  };
  server.use(
    http.get("*/api/apply/followups", () =>
      HttpResponse.json(list.enabled ? list : { ...list, items: [] }),
    ),
    http.put("*/api/apply/followups/settings", async ({ request }) => {
      const { enabled: on } = (await request.json()) as { enabled: boolean };
      list = { ...list, enabled: on };
      return HttpResponse.json({ enabled: on });
    }),
    http.post("*/api/apply/followups/:id/draft", ({ params }) => {
      const item = find(params.id);
      if (!item) return HttpResponse.json({}, { status: 404 });
      const draft = written().draft;
      if (!draft) return HttpResponse.json({}, { status: 500 });
      const done = { ...item, draft };
      list = {
        ...list,
        items: list.items.map((one) =>
          one.posting_id === item.posting_id ? done : one,
        ),
      };
      return HttpResponse.json(
        { ...item, draft: { ...draft, writing: true } },
        { status: 202 },
      );
    }),
    http.put("*/api/apply/followups/:id/draft", async ({ params, request }) => {
      const item = find(params.id);
      const text = (await request.json()) as { subject: string; body: string };
      if (!item?.draft) return HttpResponse.json({}, { status: 404 });
      return put({
        ...item,
        draft: { ...item.draft, ...text, written_by: "person", issues: [] },
      });
    }),
    http.post("*/api/apply/followups/:id/sent", ({ params }) => {
      const item = find(params.id);
      if (!item) return HttpResponse.json({}, { status: 404 });
      return put({
        ...item,
        count: item.count + 1,
        sent: [...item.sent, TODAY],
        urgency: item.count + 1 >= 2 ? "cold" : "waiting",
        next_on: item.count + 1 >= 2 ? null : "2026-11-14",
        can_draft: item.count + 1 < 2,
        draft: null,
      });
    }),
  );
}
