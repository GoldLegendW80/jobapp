/**
 * The catalogue of freelance and project sources as GET /api/opportunities/feeds answers it
 * (api/app/opportunities/sources/catalogue.py, opportunities O7), trimmed to what the web reads.
 * The default handler of src/test/server.ts answers it; a test overrides it with `server.use`.
 */
import type { FeedCatalogue } from "@/features/opportunities/sources/feeds-api";

export const FEED_CATALOGUE: FeedCatalogue = {
  supported: [
    {
      kind: "remoteok",
      name: "Remote OK",
      description:
        "Remote jobs and contract missions from the public Remote OK API.",
      attribution:
        "Each posting names Remote OK as its source and links back to it.",
      terms_url: "https://remoteok.com/api",
      checked_on: "2026-10-20",
    },
    {
      kind: "weworkremotely",
      name: "We Work Remotely",
      description: "Remote jobs from the public We Work Remotely RSS feed.",
      attribution: "Each posting links back to We Work Remotely.",
      terms_url: "https://weworkremotely.com/remote-job-rss-feed",
      checked_on: "2026-10-20",
    },
    {
      kind: "hackernews_freelance",
      name: "Hacker News: Freelancer? Seeking freelancer?",
      description:
        "Clients looking for a freelancer in the month's Hacker News thread.",
      attribution: "Each posting links back to its comment on Hacker News.",
      terms_url: "https://github.com/HackerNews/API",
      checked_on: "2026-10-20",
    },
    {
      kind: "hackernews_hiring",
      name: "Hacker News: Who is hiring?",
      description: "Companies hiring in the month's Hacker News thread.",
      attribution: "Each posting links back to its comment on Hacker News.",
      terms_url: "https://github.com/HackerNews/API",
      checked_on: "2026-10-20",
    },
  ],
  not_supported: [
    {
      kind: "malt",
      name: "Malt",
      reason:
        "No public mission board: clients contact freelancers in its own messaging.",
      terms_url: "https://www.malt.fr/legal",
      checked_on: "2026-10-20",
    },
    {
      kind: "upwork",
      name: "Upwork",
      reason:
        "Its terms forbid automated reading, and its RSS feeds ended in August 2024.",
      terms_url: "https://www.upwork.com/robots.txt",
      checked_on: "2026-10-20",
    },
  ],
};
