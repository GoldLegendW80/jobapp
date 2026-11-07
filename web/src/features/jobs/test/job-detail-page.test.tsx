import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { admin, fakeSession } from "@/test/auth";
import { csvJobs, fakeJobDetail, fakeJobsApi, makeDetail } from "@/test/jobs";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";
import { crumb } from "@/test/breadcrumb";

import type { JobFile } from "../api/jobs-api";

const JOB_ID = "00000000-0000-4000-9000-000000000001";
const PATH = `/jobs/${JOB_ID}`;

function file(name: string, mime: string, n: number): JobFile {
  return {
    id: `00000000-0000-4000-a000-${String(n).padStart(12, "0")}`,
    name,
    mime,
    size: 1536 * n,
    sha256: "ab".repeat(32),
    origin: "legacy",
    created_at: "2026-10-09T08:00:00Z",
  };
}

const CV_PDF = file("cv.pdf", "application/pdf", 1);
const CV_JSON = file("cv.json", "application/json", 2);
const ANSWERS = file("answers.json", "application/json", 3);
const APPLY = file("apply.py", "text/x-python", 4);

const pdfBytes = () =>
  new HttpResponse(new TextEncoder().encode("%PDF-1.7\n%fake\n"), {
    headers: { "Content-Type": "application/pdf" },
  });

beforeEach(() => {
  fakeSession(admin);
  window.localStorage.clear();
});

async function openDetail(path = PATH) {
  const app = renderApp(path);
  await screen.findByRole("heading", { level: 1 });
  return app;
}

/** The h1 and the line under it: company, place and pay. */
const header = () =>
  screen.getByRole("heading", { level: 1 }).parentElement as HTMLElement;
/** Status, fit, contract and work mode, at the top of the posting. */
const badges = () => screen.getByTestId("posting-badges");
/** The side rail, beside the posting on a wide page. */
const rail = () => document.querySelector("aside") as HTMLElement;
/** True when `a` comes before `b` in the page. */
const before = (a: Element, b: Element) =>
  Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

describe("job detail", () => {
  it("shows the pay set apart in the header line with separators, and the work mode in words", async () => {
    fakeJobDetail(
      makeDetail({
        pay_min: 45000,
        pay_max: 1250000,
        pay_currency: "EUR",
        pay_period: "year",
        work_mode: "office",
      }),
    );
    await openDetail();
    const pay = within(header()).getByTestId("posting-pay");
    expect(pay).toHaveTextContent("Pay: 45,000 to 1,250,000 EUR a year");
    const figures = within(pay).getByText("45,000 to 1,250,000 EUR a year");
    expect(figures).toHaveClass("font-medium", "text-foreground");
    expect(figures).not.toHaveClass("text-muted-foreground");
    expect(within(badges()).getByText("On site")).toBeInTheDocument();
  });

  it("says Pay not given, muted, when the posting states no pay, and no work mode when none", async () => {
    fakeJobDetail(makeDetail({ work_mode: null }));
    await openDetail();
    const pay = within(header()).getByTestId("posting-pay");
    expect(pay).toHaveTextContent(/^Pay not given$/);
    expect(within(pay).getByText("Pay not given")).toHaveClass(
      "text-muted-foreground",
    );
    expect(within(badges()).queryByText("Remote")).toBeNull();
    expect(within(badges()).queryByText("On site")).toBeNull();
  });

  it("says the contract in words and the fit as Fit and its number, never a code or a bare number", async () => {
    fakeJobDetail(makeDetail({ contract_type: "CDI", fit: 13 }));
    await openDetail();
    expect(within(badges()).getByText("Permanent")).toBeInTheDocument();
    expect(within(badges()).queryByText("CDI")).toBeNull();
    const fit = within(badges()).getByLabelText("Fit 13 out of 100");
    expect(fit).toHaveTextContent(/^Fit 13$/);
    expect(fit).toHaveAttribute("data-tone", "low");
  });

  it("leads with Your application, Apply first in it, then the posting in the main column and the rest in the side rail", async () => {
    fakeJobDetail(makeDetail({ posting_text: "We build robots." }));
    await openDetail();
    const region = (name: string) => screen.getByRole("region", { name });
    const application = region("Your application");
    // It opens the rail and comes before the posting in the page, so a phone shows it first.
    expect(rail()).not.toContainElement(application);
    expect(before(application, region("Posting"))).toBe(true);
    const inRail = [
      region("Documents"),
      region("Status and fit"),
      region("Agent assessment"),
      region("Details"),
    ];
    for (const one of inRail) expect(rail()).toContainElement(one);
    for (const [index, one] of inRail.slice(1).entries())
      expect(before(inRail[index]!, one)).toBe(true);
    const inMain = [
      region("Posting"),
      region("Notes"),
      region("Files"),
      region("History"),
    ];
    for (const one of inMain) expect(rail()).not.toContainElement(one);
    for (const [index, one] of inMain.slice(1).entries())
      expect(before(inMain[index]!, one)).toBe(true);
    expect(region("Posting")).toContainElement(
      screen.getByTestId("posting-text"),
    );
    const apply = await within(application).findByRole("button", {
      name: "Apply",
    });
    expect(apply).toBeVisible();
    expect(
      before(
        apply,
        within(application).getByRole("button", { name: "Apply myself" }),
      ),
    ).toBe(true);
  });

  it("shows status and fit, the assessment, notes, files and history at once, without a fold", async () => {
    fakeJobDetail(makeDetail());
    await openDetail();
    expect(
      screen.queryByRole("button", { name: "More about this posting" }),
    ).toBeNull();
    for (const name of ["Status and fit", "Agent assessment"])
      expect(screen.getByRole("region", { name })).toBeVisible();
    expect(screen.getByRole("textbox", { name: "Notes" })).toBeVisible();
    expect(screen.getByText("No files attached.")).toBeVisible();
    expect(
      screen.getByRole("list", { name: "History, newest first" }),
    ).toBeVisible();
  });

  it("keeps one h1, the posting's title, and puts every section a level below it", async () => {
    fakeJobDetail(makeDetail({ title: "Applied AI Engineer" }));
    await openDetail();
    expect(screen.getAllByRole("heading", { level: 1 })).toEqual([
      screen.getByRole("heading", { name: "Applied AI Engineer" }),
    ]);
    const article = screen.getByRole("article", {
      name: "Applied AI Engineer",
    });
    for (const name of ["Your application", "Posting", "Notes", "History"])
      expect(
        within(article).getByRole("heading", { level: 2, name }),
      ).toBeInTheDocument();
  });

  it("shows the header: company, title, location, badges, dates, source and link", async () => {
    fakeJobDetail(
      makeDetail({
        company: "Mistral AI",
        title: "Applied AI Engineer",
        location: "Paris, France",
        contract_type: "CDI",
        status: "cv_ready",
        fit: 88,
        competencies: ["agents", "llm_apps"],
        gaps: ["years"],
        found_on: "2026-10-05",
        published_on: "2026-09-30",
        applied_on: null,
        source: "ashby:mistral",
        url: "https://jobs.ashbyhq.com/mistral/123",
      }),
    );
    await openDetail();
    expect(
      screen.getByRole("heading", { level: 1, name: "Applied AI Engineer" }),
    ).toBeInTheDocument();
    expect(header()).toHaveTextContent("Mistral AI, Paris, France");
    expect(
      screen.getByText("CV ready", { selector: "[data-status]" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Status")).toHaveValue("cv_ready");
    expect(screen.getByLabelText("Fit 88 out of 100")).toBeInTheDocument();
    expect(
      within(screen.getByRole("list", { name: "Competencies" })).getAllByRole(
        "listitem",
      ),
    ).toHaveLength(2);
    expect(
      within(screen.getByRole("list", { name: "Gaps" })).getByText("years"),
    ).toBeInTheDocument();
    expect(screen.getByText("5 Oct 2026")).toBeInTheDocument();
    expect(screen.getByText("30 Sept 2026")).toBeInTheDocument();
    expect(screen.queryByText("Applied", { selector: "dt" })).toBeNull();
    expect(screen.getByText("ashby:mistral")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /jobs\.ashbyhq\.com/ });
    expect(link).toHaveAttribute(
      "href",
      "https://jobs.ashbyhq.com/mistral/123",
    );
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(document.title).toBe("Applied AI Engineer at Mistral AI · jobapp");
  });

  it("says via which feed a mission came and links back to it there (O8)", async () => {
    fakeJobDetail(
      makeDetail({
        source: "remoteok, greenhouse:acme",
        url: "https://remoteok.com/remote-jobs/123-ai-engineer",
        contract_type: "Freelance",
      }),
    );
    const app = await openDetail();
    const source = screen.getByText("Source").nextSibling as HTMLElement;
    const via = within(source).getByRole("link", { name: /via Remote OK/ });
    expect(via).toHaveAttribute(
      "href",
      "https://remoteok.com/remote-jobs/123-ai-engineer",
    );
    expect(via).toHaveAttribute("target", "_blank");
    expect(source).toHaveTextContent(
      "via Remote OK(opens in a new tab); greenhouse:acme",
    );
    app.unmount();
    fakeJobDetail(
      makeDetail({
        source: "hackernews:freelance",
        url: "https://news.ycombinator.com/item?id=4001",
      }),
    );
    await openDetail();
    expect(
      within(screen.getByText("Source").nextSibling as HTMLElement).getByRole(
        "link",
        { name: /via Hacker News, freelance thread/ },
      ),
    ).toHaveAttribute("href", "https://news.ycombinator.com/item?id=4001");
  });

  it("keeps the paragraphs of the posting", async () => {
    fakeJobDetail(makeDetail());
    await openDetail();
    const posting = screen.getByTestId("posting-text");
    const paragraphs = posting.querySelectorAll("p");
    expect(paragraphs).toHaveLength(2);
    expect(paragraphs[0]).toHaveTextContent("First paragraph.");
    expect(paragraphs[1]!.textContent).toBe(
      "Second paragraph\nwith a line break.",
    );
  });

  it("says so when there is no posting text, and shows the summary instead", async () => {
    fakeJobDetail(
      makeDetail({ posting_text: null, description: "Agents for banks." }),
    );
    await openDetail();
    expect(
      screen.getByText(
        /No posting text was saved for this job\. Summary: Agents for banks\./,
      ),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("posting-text")).toBeNull();
  });

  it("treats a blank posting text as none", async () => {
    fakeJobDetail(makeDetail({ posting_text: "  \n\n " }));
    await openDetail();
    expect(screen.getByText(/No posting text was saved/)).toBeInTheDocument();
  });

  it("renders HTML and entities in the posting as text, never as markup", async () => {
    fakeJobDetail(
      makeDetail({
        title: "<img src=x onerror=window.__xss=1>",
        notes: "<b>bold?</b>",
        posting_text:
          '<script>window.__xss = 2</script>\n\nFish &amp; chips &lt;tag&gt; &#x27;\n\n<a href="javascript:alert(1)">click</a>',
        legacy: { notes_html: "<iframe src=//evil></iframe>" },
      }),
    );
    const { container } = await openDetail();
    expect(
      screen.getByText("<script>window.__xss = 2</script>"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Fish &amp; chips &lt;tag&gt; &#x27;"),
    ).toBeInTheDocument();
    expect(
      screen.getByText('<a href="javascript:alert(1)">click</a>'),
    ).toBeInTheDocument();
    expect(screen.getByText("<b>bold?</b>")).toBeInTheDocument();
    expect(
      container.querySelector(
        "article script, article img, article iframe, article b",
      ),
    ).toBeNull();
    expect(screen.queryByRole("link", { name: "click" })).toBeNull();
    expect((window as { __xss?: number }).__xss).toBeUndefined();
  });

  it.each([
    "javascript:alert(document.cookie)",
    " JAVASCRIPT:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "ftp://example.org/file",
    "jobs.example.org/no-scheme",
  ])("does not make %s a link", async (url) => {
    fakeJobDetail(makeDetail({ url }));
    await openDetail();
    const posting = screen.getByTestId("posting-link");
    expect(within(posting).queryByRole("link")).toBeNull();
    expect(within(posting).getByText(url.trim())).toBeInTheDocument();
    for (const link of screen.getAllByRole("link"))
      expect(link.getAttribute("href") ?? "").not.toMatch(
        /^\s*(javascript|data):/i,
      );
  });

  it("shows no link at all when the job has no url", async () => {
    fakeJobDetail(makeDetail({ url: null }));
    await openDetail();
    expect(screen.queryByTestId("posting-link")).toBeNull();
  });

  it("falls back to a readable label for an unknown status", async () => {
    fakeJobDetail(makeDetail({ status: "offer_received" }));
    await openDetail();
    expect(
      screen.getByText("Offer received", { selector: "[data-status]" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Status")).toHaveValue("offer_received");
  });

  it("lists the files with their icons, sizes and links", async () => {
    fakeJobDetail(
      makeDetail({ files: [CV_PDF, CV_JSON, ANSWERS, APPLY], file_count: 4 }),
    );
    await openDetail();
    const files = screen.getByRole("list", { name: "Files" });
    const items = within(files).getAllByRole("listitem");
    expect(
      items.map((item) => item.querySelector(".font-medium")?.textContent),
    ).toEqual(["cv.pdf", "cv.json", "answers.json", "apply.py"]);
    expect(within(items[0]!).getByText("1.5 KB, imported")).toBeInTheDocument();
    const open = screen.getByRole("link", { name: "Open cv.pdf" });
    expect(open).toHaveAttribute(
      "href",
      `/api/jobs/${JOB_ID}/files/${CV_PDF.id}`,
    );
    expect(open).toHaveAttribute("target", "_blank");
    expect(open).toHaveAttribute("rel", "noopener noreferrer");
    const download = screen.getByRole("link", { name: "Download apply.py" });
    expect(download).toHaveAttribute("download", "apply.py");
    // Every icon is decorative.
    for (const icon of files.querySelectorAll("svg"))
      expect(icon).toHaveAttribute("aria-hidden", "true");
    // Preview only for PDFs, the JSON viewer only for JSON.
    expect(screen.getAllByRole("button", { name: /preview of/i })).toHaveLength(
      1,
    );
    expect(screen.getByText("Show cv.json")).toBeInTheDocument();
    expect(screen.queryByText("Show apply.py")).toBeNull();
  });

  it("says when there are no files", async () => {
    fakeJobDetail(makeDetail({ files: [] }));
    await openDetail();
    expect(screen.getByText("No files attached.")).toBeInTheDocument();
  });

  it("previews a PDF inline from a blob once asked, and frees it when hidden", async () => {
    const created = vi.fn(() => "blob:http://localhost/preview-1");
    const revoked = vi.fn();
    Object.assign(URL, { createObjectURL: created, revokeObjectURL: revoked });
    const api = fakeJobDetail(makeDetail({ files: [CV_PDF] }), {
      [CV_PDF.id]: pdfBytes,
    });
    const user = userEvent.setup();
    await openDetail();
    // Nothing is downloaded until the preview is asked for.
    expect(api.fileCalls).toEqual([]);
    await user.click(
      screen.getByRole("button", { name: "Show preview of cv.pdf" }),
    );
    const frame = await screen.findByTitle("Preview of cv.pdf");
    expect(frame.tagName).toBe("IFRAME");
    expect(frame).toHaveAttribute("src", "blob:http://localhost/preview-1");
    expect(api.fileCalls).toEqual([CV_PDF.id]);
    const blob = (created.mock.calls[0] as unknown as [Blob])[0];
    expect(blob.type).toBe("application/pdf");
    await user.click(
      screen.getByRole("button", { name: "Hide preview of cv.pdf" }),
    );
    expect(screen.queryByTitle("Preview of cv.pdf")).toBeNull();
    expect(revoked).toHaveBeenCalledWith("blob:http://localhost/preview-1");
  });

  it("refuses to preview bytes that are not a PDF and offers the link", async () => {
    const created = vi.fn(() => "blob:x");
    Object.assign(URL, { createObjectURL: created, revokeObjectURL: vi.fn() });
    fakeJobDetail(makeDetail({ files: [CV_PDF] }), {
      [CV_PDF.id]: () =>
        new HttpResponse("<html><script>alert(1)</script></html>", {
          headers: { "Content-Type": "application/pdf" },
        }),
    });
    const user = userEvent.setup();
    await openDetail();
    await user.click(
      screen.getByRole("button", { name: "Show preview of cv.pdf" }),
    );
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      "The preview could not be shown. This file is not a PDF.",
    );
    expect(
      within(alert).getByRole("link", { name: "Open cv.pdf" }),
    ).toBeInTheDocument();
    expect(created).not.toHaveBeenCalled();
    expect(screen.queryByTitle("Preview of cv.pdf")).toBeNull();
  });

  it("falls back to the link when the PDF cannot be fetched", async () => {
    Object.assign(URL, { createObjectURL: vi.fn(), revokeObjectURL: vi.fn() });
    fakeJobDetail(makeDetail({ files: [CV_PDF] }));
    const user = userEvent.setup();
    await openDetail();
    await user.click(
      screen.getByRole("button", { name: "Show preview of cv.pdf" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This file could not be loaded.",
    );
  });

  it("shows a JSON file pretty-printed once opened, and only fetches it then", async () => {
    const api = fakeJobDetail(makeDetail({ files: [CV_JSON] }), {
      [CV_JSON.id]: () =>
        HttpResponse.json({
          headline: "<b>AI</b> Engineer",
          highlights: ["one", "two"],
        }),
    });
    const user = userEvent.setup();
    await openDetail();
    expect(api.fileCalls).toEqual([]);
    await user.click(screen.getByText("Show cv.json"));
    const content = await screen.findByLabelText("Content of cv.json");
    expect(content.textContent).toBe(
      '{\n  "headline": "<b>AI</b> Engineer",\n  "highlights": [\n    "one",\n    "two"\n  ]\n}',
    );
    expect(content.querySelector("b")).toBeNull();
    expect(api.fileCalls).toEqual([CV_JSON.id]);
  });

  it("reports a JSON file that fails to load", async () => {
    fakeJobDetail(makeDetail({ files: [ANSWERS] }));
    const user = userEvent.setup();
    await openDetail();
    await user.click(screen.getByText("Show answers.json"));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "answers.json could not be loaded. File not found.",
    );
  });

  it("shows the events, newest first, and says when older ones are left out", async () => {
    fakeJobDetail(
      makeDetail({
        events: [
          {
            id: 3,
            at: "2026-10-09T10:00:00Z",
            kind: "status",
            data: { from: "found", to: "applied" },
          },
          {
            id: 2,
            at: "2026-10-08T10:00:00Z",
            kind: "fit",
            data: { from: 70, to: 85 },
          },
          {
            id: 1,
            at: "2026-10-07T10:00:00Z",
            kind: "created",
            data: { via: "import" },
          },
        ],
        events_total: 60,
      }),
    );
    await openDetail();
    const history = screen.getByRole("list", { name: "History, newest first" });
    expect(
      within(history)
        .getAllByRole("listitem")
        .map((li) => li.firstElementChild?.nextSibling?.textContent),
    ).toEqual([
      "Status changed from Found to Applied",
      "Fit changed from 70 to 85",
      "Imported",
    ]);
    expect(
      screen.getByText("Showing the newest 3 of 60 events."),
    ).toBeInTheDocument();
  });

  it("keeps the imported fields in a collapsed section", async () => {
    fakeJobDetail(
      makeDetail({
        legacy: { report: "data/reports/x.html", extra: { a: 1 } },
      }),
    );
    await openDetail();
    expect(
      screen.getByRole("region", { name: "Imported fields" }),
    ).toBeInTheDocument();
    const trigger = screen.getByRole("button", { name: "Show the 2 fields" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("data/reports/x.html")).toBeNull();
    await userEvent.setup().click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(trigger).toHaveTextContent("Hide the 2 fields");
    const details = document.getElementById(
      trigger.getAttribute("aria-controls")!,
    )!;
    expect(
      within(details).getByText("data/reports/x.html"),
    ).toBeInTheDocument();
    expect(
      within(details).getByText('{ "a": 1 }', {
        normalizer: (t) => t.replace(/\s+/g, " "),
      }),
    ).toBeInTheDocument();
  });

  it("hides the imported fields section when there are none", async () => {
    fakeJobDetail(makeDetail({ legacy: {} }));
    await openDetail();
    expect(
      screen.queryByRole("region", { name: "Imported fields" }),
    ).toBeNull();
  });

  it("shows the not found page for a job that is not there (or not yours)", async () => {
    fakeJobDetail(makeDetail());
    renderApp("/jobs/00000000-0000-4000-9000-000000000999");
    expect(
      await screen.findByRole("heading", { name: "Page not found" }),
    ).toBeInTheDocument();
  });

  it("reports a server error and retries", async () => {
    let calls = 0;
    const job = makeDetail();
    server.use(
      http.get("*/api/jobs/:id", () => {
        calls += 1;
        return calls === 1
          ? HttpResponse.json({ detail: "boom" }, { status: 500 })
          : HttpResponse.json(job);
      }),
    );
    const user = userEvent.setup();
    renderApp(PATH);
    expect(
      await screen.findByText(
        "This posting could not be loaded. Something went wrong on the server. Try again in a moment.",
      ),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(
      await screen.findByRole("heading", { level: 1, name: job.title }),
    ).toBeInTheDocument();
  });
});

describe("back to the list", () => {
  it("returns to the same filters and the same posting it came from", async () => {
    fakeJobsApi(csvJobs());
    fakeJobDetail(makeDetail());
    vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    const user = userEvent.setup();
    const { location } = renderApp("/jobs?status=found&q=Engineer&sort=fit");
    const list = await screen.findByRole("list", { name: "Postings" });
    const item = (await within(list).findAllByRole("listitem")).find(
      (r) => r.getAttribute("data-index") === "0",
    )!;
    await user.click(within(item).getByRole("link", { name: "Engineer 1" }));
    await screen.findByRole("heading", { level: 1, name: "Engineer 1" });
    expect(location()).toBe(PATH);
    const back = crumb("Jobs");
    expect(back).toHaveAttribute(
      "href",
      `/jobs?q=Engineer&status=found&sort=fit&job=${JOB_ID}`,
    );
    await user.click(back);
    await waitFor(() => {
      expect(location()).toBe(
        `/jobs?q=Engineer&status=found&sort=fit&job=${JOB_ID}`,
      );
    });
    expect(
      screen.getByRole("searchbox", { name: "Search postings" }),
    ).toHaveValue("Engineer");
    expect(
      await within(
        screen.getByRole("region", { name: "Selected posting" }),
      ).findByRole("heading", { level: 2, name: "Engineer 1" }),
    ).toBeVisible();
  });

  it("goes to the plain list when opened directly", async () => {
    fakeJobDetail(makeDetail());
    await openDetail();
    expect(crumb("Jobs")).toHaveAttribute("href", "/jobs");
  });

  it("ignores a crafted state that is not a list query", async () => {
    fakeJobDetail(makeDetail());
    const app = await openDetail();
    await act(async () => {
      await app.router.navigate(PATH, { state: { from: "//evil.example" } });
    });
    expect(crumb("Jobs")).toHaveAttribute("href", "/jobs");
  });
});
