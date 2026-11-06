# web/ architecture

The web app of jobapp: React 18, TypeScript, Vite, Tailwind v4, shadcn (Radix), TanStack Query,
React Router 7. This page is the map: where a file goes, what it may import, and why.

## Three rules

1. **A page is a composition of components, with its data fetched through the api.** A page file
   lays out components; the data comes from hooks of a feature's `api/` folder.
2. **A component used by two pages or more lives in `src/components/shared/`**, in the folder of
   the feature whose data it shows. A component used by one page lives in that page's feature.
3. **All fetching lives in `api/`.** Requests, query keys, response schemas and every
   `useQuery` or `useMutation` hook sit in a feature's `api/` folder, and nowhere else.

## The folders

```
src/
  app/                    the application
    main.tsx              entry point (index.html loads it), the query client
    app.tsx               the router
    routes.tsx            every route; each page is loaded on demand (React.lazy)
    root-layout.tsx       what every page shares: session, tooltips, toasts, scroll
    layouts/              the signed in frame: header, navigation, user menu, phone tab bar
    test/                 the tests of app/
  components/
    ui/                   shadcn primitives, restyled by the design system
    blocks/               generic compositions: PageHeader, PillNav, Brand, LoadingCard, ...
    shared/<feature>/     components used by two pages or more, by the feature that owns their
                          data: shared/jobs/ (fit badge, the posting), shared/apply/ (Apply), ...
    stories/              the stories of ui/, blocks/ and shared/
    test/                 the tests of ui/, blocks/ and shared/
  features/<feature>/     one folder per product area: jobs, apply, profile, documents, ...
    api/                  requests, query keys, schemas (zod) and the hooks that fetch:
                          use-jobs.ts, use-application.ts, ...
    components/           the components of this feature's pages, each used by one page
    helpers/              pure functions, no React: labels, formatting, parsing, rules
    hooks/                React hooks that fetch nothing: UI state, keyboard, media
    pages/                route components: compositions of components and api hooks
    stories/              Storybook: a page's stories are its user stories
    test/                 Vitest: the tests of this feature's files
  hooks/                  hooks shared by many features (media queries, the page title)
  lib/                    shared, React free: the HTTP client, cn(), formats, the app's words
  design-system/          Storybook only: foundations, patterns, fixtures, story helpers
  test/                   test support: the fake api (MSW), fixtures, render helpers, setup
  index.css               the design tokens (Storybook, Foundations)
```

A feature may have no `pages/` (mail, browser): its data is shown by other features' pages
through its `api/` and its shared components.

## What may import what

`eslint.config.js` enforces this table (`no-restricted-imports`); tests and stories are exempt.

| Folder                                  | May import                                               | Never                                           |
| --------------------------------------- | -------------------------------------------------------- | ----------------------------------------------- |
| `lib/`, `hooks/`                        | each other, packages                                     | components, features, app                       |
| `components/ui/`, `components/blocks/`  | `lib/`, `hooks/`                                         | shared components, features, app                |
| `features/*/api/`, `helpers/`, `hooks/` | `lib/`, `hooks/`, other features' api, helpers and hooks | any component, any page, app                    |
| `components/shared/`                    | the above, and other shared components                   | a feature's own components, pages, app          |
| `features/<f>/components/`              | the above, and `<f>`'s own components                    | another feature's components, pages, app        |
| `features/<f>/pages/`                   | the above, and `<f>`'s own components                    | another feature's components, another page, app |
| `app/`                                  | everything                                               |                                                 |

There is no `index.ts` per feature on purpose: a barrel file makes every import load the whole
module (Vercel's `bundle-barrel-imports`).

## Where a new file goes

1. A request, a query key or a hook that fetches: `features/<owner>/api/`.
2. A pure function: `features/<f>/helpers/` (used by one feature) or `lib/` (used by many).
3. A component used by one page: `features/<that page's feature>/components/`.
4. A component used by two pages or more: `components/shared/<owner feature>/`. When a second
   page starts using a feature component, move it there.
5. A new page: `features/<f>/pages/`, a line in `app/routes.tsx` with `lazy()`, and a story file
   in `features/<f>/stories/` whose stories are the page's user stories.
6. Its test in the `test/` folder of the same area, named after the file (`x.tsx`,
   `test/x.test.tsx`); its story in the `stories/` folder beside it.

## How data flows

page or component → hook of `features/<f>/api/` (`useJobs(filters)`) → request function of the
same folder → `lib/api.ts` (the HTTP client: credentials, errors as readable sentences) → the API.
TanStack Query caches every answer by the keys each `api/` file exports; a mutation hook updates
or invalidates the keys it changes. No global store: server state lives in the query cache, page
state in the address (filters, the selected posting) or in the component.

## Performance

- Every page is its own chunk (`app/routes.tsx`, `React.lazy`): the first visit downloads the page
  it opens, not the others. Libraries are long cached chunks of their own (`vite.config.ts`).
- No barrel files, no component defined inside another component, long lists virtualized.

## Tests and stories

- Vitest runs `src/**/*.test.{ts,tsx}` against the fake api of `src/test/`.
- `pnpm storybook`: page stories render the real routes with the same fake api
  (`src/design-system/app-at.tsx`), so a page in Storybook is the page of the app.
- Playwright journeys in `web/e2e/` run against the real stack.
