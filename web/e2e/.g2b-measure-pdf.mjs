// Temporary, not committed: PDFs as a browser prints them (embedded fonts, object streams),
// of 1, 20 and 200 pages, for the measurement of the PDF reader. argv[2]: output directory.
import { chromium } from "@playwright/test";
import { writeFileSync } from "node:fs";

const out = process.argv[2];
const browser = await chromium.launch();
const page = await browser.newPage();
const paragraph =
  "Robin Example led the migration of the billing service to Postgres in 2021, cut the nightly " +
  "batch from four hours to forty minutes, and mentored three engineers. Stack: Python, FastAPI, " +
  "SQLAlchemy, Redis, React, TypeScript, Docker, Terraform. Éducation: Université de Nantes. ";
for (const pages of [1, 20, 200]) {
  const body = Array.from(
    { length: pages },
    (_, n) =>
      `<section style="page-break-after: always"><h1>Robin Example, page ${n + 1}</h1>` +
      `<h2 style="font-family: Georgia, serif">Experience</h2>` +
      Array.from(
        { length: 9 },
        (_, k) =>
          `<p style="font-family: ${k % 2 ? "Helvetica" : "Georgia"}; font-size: 11pt">${paragraph}<b>${paragraph}</b>` +
          `<a href="https://example.org/robin/${n}/${k}">link ${k}</a></p>`,
      ).join("") +
      `<table border="1"><tr><td>Skill</td><td>Years</td></tr><tr><td>Python</td><td>6</td></tr></table></section>`,
  ).join("");
  await page.setContent(`<html><body>${body}</body></html>`);
  const pdf = await page.pdf({ format: "A4", printBackground: true });
  writeFileSync(`${out}/real-${pages}.pdf`, pdf);
  console.log(pages, pdf.length);
}
await browser.close();
