# Evidence: jobs J7, the list, detail and map journeys on the Jetson

Nothing here yet but this file: the owner's run fills the folder. On his Mac, in the app's
checkout, where `ssh jetson` works:

```bash
OWNER=<the owner's account e-mail> tests/e2e_jobs_jetson.sh view
```

The run goes through the public URL (`./deploy.sh url`) and uses synthetic data only
(`E2E_SYNTHETIC=1`, loaded over HTTP):

- `web/e2e/jobs.spec.ts`: an e2e account `e2e-jobs-<run>@example.org` receives 433 made-up rows
  with the CSV counts (posting texts, a one line `cv.pdf` and a `cv.json` on the staged, applied
  and interview rows), then the counts, filters, a shared URL, infinite scroll, a detail with its
  files and PDF preview, the way back, the keyboard and the phone layout;
- `web/e2e/jobs-map.spec.ts`: an e2e account `e2e-jobs-map-<run>@example.org` receives 19 made-up
  postings, most of them in real cities, then the pins, a place's panel, a status filter, the unplaced list, the
  keyboard zoom and the phone layout.

What it writes here:

- `run.log`, with the owner's address replaced by "(the owner's address)";
- the screenshots, light and dark at 1280 px and 375 px: `jobs-empty`, `jobs-dashboard`,
  `jobs-filtered`, `jobs-detail` and `jobs-cards-mobile.png`, then `map-map`, `map-panel`,
  `map-filtered` and `map-europe`. The detail is photographed before its PDF preview opens.

A passing run ends its log with the owner's jobs identical before and after, every e2e count at 0
after the cleanup, no failed sign in of the owner's account in the 15 minute window, and PASS.
