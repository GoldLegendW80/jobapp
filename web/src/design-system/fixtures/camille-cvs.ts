import { http, HttpResponse } from "msw";

import type { BaseCv } from "@/features/documents/api/base-cvs-api";
import { fakeCv } from "@/test/documents";
import { server } from "@/test/server";

/** Camille's two base CVs: the default one written from her profile, and a French copy. */
export function profileCvs(): BaseCv[] {
  return [
    fakeCv({
      id: "cv-camille",
      name: "Data engineer CV",
      is_default: true,
      last_version_at: "2026-11-04T17:20:00Z",
      updated_at: "2026-11-04T17:20:00Z",
    }),
    fakeCv({
      id: "cv-camille-fr",
      name: "CV en français",
      source: "built",
      language: "fr",
      template_slug: "modern",
      is_default: false,
      last_version_at: "2026-10-28T09:10:00Z",
      updated_at: "2026-10-28T09:10:00Z",
      generation: null,
    }),
  ];
}

const PAGE = `<svg xmlns="http://www.w3.org/2000/svg" width="900" height="1273" viewBox="0 0 900 1273">
<rect width="900" height="1273" fill="#ffffff"/>
<text x="72" y="120" font-family="Helvetica, Arial, sans-serif" font-size="40" font-weight="700" fill="#141414">Camille Martin</text>
<text x="72" y="164" font-family="Helvetica, Arial, sans-serif" font-size="22" fill="#555">Senior data engineer, Lyon</text>
<text x="72" y="196" font-family="Helvetica, Arial, sans-serif" font-size="18" fill="#777">camille@example.org</text>
<rect x="72" y="236" width="756" height="2" fill="#e8e8e8"/>
<text x="72" y="292" font-family="Helvetica, Arial, sans-serif" font-size="20" font-weight="700" fill="#141414">Experience</text>
<text x="72" y="336" font-family="Helvetica, Arial, sans-serif" font-size="18" font-weight="700" fill="#141414">Lumen Freight, senior data engineer</text>
<text x="72" y="364" font-family="Helvetica, Arial, sans-serif" font-size="16" fill="#777">April 2022 to today, Lyon</text>
<rect x="72" y="388" width="640" height="10" rx="5" fill="#ececec"/>
<rect x="72" y="410" width="700" height="10" rx="5" fill="#ececec"/>
<rect x="72" y="432" width="520" height="10" rx="5" fill="#ececec"/>
<text x="72" y="492" font-family="Helvetica, Arial, sans-serif" font-size="18" font-weight="700" fill="#141414">Vellum Analytics, data engineer</text>
<text x="72" y="520" font-family="Helvetica, Arial, sans-serif" font-size="16" fill="#777">September 2019 to March 2022, Lyon</text>
<rect x="72" y="544" width="690" height="10" rx="5" fill="#ececec"/>
<rect x="72" y="566" width="600" height="10" rx="5" fill="#ececec"/>
<text x="72" y="640" font-family="Helvetica, Arial, sans-serif" font-size="20" font-weight="700" fill="#141414">Skills</text>
<text x="72" y="680" font-family="Helvetica, Arial, sans-serif" font-size="17" fill="#333">Python, SQL, dbt, Airflow, Kafka, Flink, BigQuery, Terraform</text>
<text x="72" y="752" font-family="Helvetica, Arial, sans-serif" font-size="20" font-weight="700" fill="#141414">Education</text>
<text x="72" y="792" font-family="Helvetica, Arial, sans-serif" font-size="17" fill="#333">Master of Engineering, data science, 2019</text>
</svg>`;

/** The page image of any CV version: a drawn page, since Storybook has no renderer. */
export function fakeCvPages() {
  server.use(
    http.get(
      "*/api/documents/:id/versions/:number/page:page.png",
      () =>
        new HttpResponse(PAGE, {
          headers: { "Content-Type": "image/svg+xml" },
        }),
    ),
  );
}
