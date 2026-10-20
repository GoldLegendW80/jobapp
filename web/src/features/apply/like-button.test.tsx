import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { fakeApplication } from "@/test/apply";
import { fakeQueue } from "@/test/apply-queue";
import { fakeSession, member } from "@/test/auth";
import { fakeJobsApi, makeJob } from "@/test/jobs";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import { LIKE_MAX } from "./queue-state";

const SAME_ROLE =
  "An application to the same role at this company is already under way.";

/** POST /api/postings/{id}/like: 201, or 409 with the api's sentence for the ids in `refuse`. */
function fakeLikes(refuse: ReadonlySet<string> = new Set()) {
  const liked: string[] = [];
  server.use(
    http.post("*/api/postings/:id/like", ({ params }) => {
      const id = String(params.id);
      liked.push(id);
      if (refuse.has(id)) {
        return HttpResponse.json(
          { detail: SAME_ROLE, code: "same_role_live", application_id: null },
          { status: 409 },
        );
      }
      return HttpResponse.json(
        {
          application: fakeApplication("queued", { posting_id: id }),
          documents: ["cv", "letter"],
        },
        { status: 201 },
      );
    }),
  );
  return liked;
}

const bar = () => screen.getByRole("region", { name: "Bulk actions" });

async function openJobs(count: number) {
  const rows = Array.from({ length: count }, (_, i) => makeJob(i + 1));
  fakeJobsApi(rows);
  const app = renderApp("/jobs");
  const table = await screen.findByRole("table", { name: "Postings" });
  await within(table).findAllByRole("row");
  const boxes = within(table)
    .getAllByRole("checkbox")
    .filter((box) => / at /.test(box.getAttribute("aria-label") ?? ""));
  return { rows, app, boxes };
}

beforeEach(() => {
  fakeSession(member);
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
});

describe("Like on the jobs list", () => {
  it("likes each selected posting, says where the applications are, and clears the selection", async () => {
    fakeQueue([]);
    const liked = fakeLikes();
    const user = userEvent.setup();
    const { rows, app, boxes } = await openJobs(3);
    await user.click(boxes[0]!);
    await user.click(boxes[2]!);

    await user.click(within(bar()).getByRole("button", { name: "Like" }));
    await screen.findByText(
      "2 postings liked. The assistant fills each form, then waits for your approval in Applications.",
    );
    expect(liked).toEqual([rows[0]!.id, rows[2]!.id]);
    expect(screen.queryByRole("region", { name: "Bulk actions" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "Open Applications" }));
    await waitFor(() => {
      expect(app.location()).toBe("/applications");
    });
  });

  it("shows the api's sentence for a refused Like, and the others still go", async () => {
    fakeQueue([]);
    const { rows, boxes } = await openJobs(3);
    const liked = fakeLikes(new Set([rows[1]!.id]));
    const user = userEvent.setup();
    for (const box of boxes) await user.click(box);

    await user.click(within(bar()).getByRole("button", { name: "Like" }));
    await screen.findByText(`1 posting not liked: ${SAME_ROLE}`);
    await screen.findAllByText(/^2 postings liked\./);
    expect(liked).toHaveLength(3);
  });

  it(`takes at most ${LIKE_MAX} postings a click, and says so`, async () => {
    fakeQueue([]);
    const liked = fakeLikes();
    const user = userEvent.setup();
    const { rows } = await openJobs(LIKE_MAX + 2);
    await user.click(
      screen.getByRole("checkbox", {
        name: "Select every posting in this view",
      }),
    );

    const like = await within(bar()).findByRole("button", {
      name: `Like the first ${LIKE_MAX}`,
    });
    await user.click(like);
    await screen.findByText(new RegExp(`^${LIKE_MAX} postings liked\\.`));
    expect(liked).toEqual(rows.slice(0, LIKE_MAX).map((row) => row.id));
  });
});
