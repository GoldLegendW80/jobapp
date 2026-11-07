# web/ architecture

The web app of jobapp: React 18, TypeScript, Vite, Tailwind v4, shadcn (Radix), TanStack Query,
React Router 7. This page is the map: where a file goes, what it may import, and why.

## Three rules

1. **A page is a composition of components, with its data fetched through the api.** A page file
   lays out components; the data comes from hooks of a feature's `api/` folder.
2. **A component used by two pages or more lives in `src/components/shared/`**, in the folder of
   the feature whose data it shows. A component used by one page lives in that page's feature.
3. **All fetching lives in `api/`.** Requests, query keys, response schemas, every `useQuery` or
   `useMutation` hook and every read or write of the query cache sit in a feature's `api/` folder,
   and nowhere else. A component calls a hook, never a request.

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
    assets/               files a page imports: Discover's built Rive scenes (discover.riv)
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
rive/<scene>/             Rive sources (RML), built with the Rive CLI into a feature's assets/:
                          `rive rive/discover --once` (no Luau scripts: web runtimes refuse them unsigned)
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

Outside `api/`, importing `useQuery`, `useMutation`, `useQueryClient` and their siblings from
TanStack Query is an error too: a component that needs data calls a hook of `api/`.

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

## How a page is built

Every signed-in page has one structure (Storybook: Patterns, Page structure):

```tsx
<Page>
  <PageHeader trail={[...]} title="..." description="..." actions={...} />
  <PageTabs label="..." tabs={[...]}>          {/* only when the area has several views */}
    <PageBody aside={...}>                      {/* aside: the side rail, when there is one */}
      <Section title="..." description="..." actions={...}>
        <Card>...</Card>                         {/* one surface per section, never nested */}
      </Section>
    </PageBody>
  </PageTabs>
</Page>
```

- **Width:** the page spans the shell (1440 px at most). Text keeps short lines; the width goes to
  columns: a workspace uses it whole, an item puts what accompanies it in the side rail, settings
  use `Section layout="side"` (the group's name on the left, its controls on the right).
- **Views:** List and Map, Search and Applications, Users and Agents are `PageTabs` (shadcn Tabs),
  each at its own address. A page below others has their breadcrumb instead, `trail` in its
  header: the pages above from the top, named as they name themselves ("Jobs", "Profile"), then
  the page itself; a phone shows only the last page above, with an arrow.
- **Actions:** one primary per header and per section, first; two others at most beside it, the
  rest in a menu. Secondary actions are `outline` buttons; `ghost` is for icon buttons and dense
  rows, `link` for a link inside a sentence.
- **Item pages:** `PageBody lead` holds what the person must reach first (Your application on a
  posting): it opens the rail on a wide screen and comes before the main column on a phone. Facts
  about the item (dates, figures) are `Facts` of `Fact`s.
- **States:** `LoadError` for what could not be loaded (with Retry, and `reason` when the server's
  own words must not show), `EmptyState` for a view with nothing in it (with the action that fills
  it), skeletons or `LoadingCard` while loading.

## How data flows

page or component → hook of `features/<f>/api/` (`useJobs(filters)`) → request function of the
same folder → `lib/api.ts` (the HTTP client: credentials, errors as readable sentences) → the API.
TanStack Query caches every answer by the keys each `api/` file exports; a mutation hook updates
or invalidates the keys it changes. A mutation hook takes the call site's own reactions (a toast,
closing a dialog, a navigation) as `onSuccess`/`onError` and calls them after its cache work, so
they still run when the component that started it has unmounted. No global store: server state lives in the query cache, page
state in the address (filters, the selected posting) or in the component.

## Performance

- Every page is its own chunk (`app/routes.tsx`, `React.lazy`): the first visit downloads the page
  it opens, not the others. Libraries are long cached chunks of their own (`vite.config.ts`).
- No barrel files, no component defined inside another component, long lists virtualized.

## Tests and stories

- Vitest runs `src/**/*.test.{ts,tsx}` against the fake api of `src/test/`.
- `pnpm storybook`: page stories render the real routes with the same fake api
  (`src/design-system/app-at.tsx`), so a page in Storybook is the page of the app. A shared
  component's story wraps it in `withApp` (`src/design-system/with-app.tsx`: a query client and
  a router) and fakes its requests with `server.use`; stories live in
  `src/components/stories/shared/<feature>/`, under Shared in Storybook.
- Agentation (`src/app/dev-feedback.tsx`, development only, also in Storybook): click an element,
  write a note, and it reaches Claude Code through the agentation MCP server on port 4747
  (`claude mcp add --scope local agentation -- npx -y agentation-mcp@1.3.2 server`).
- Playwright journeys in `web/e2e/` run against the real stack.
