import { lazy } from "react";
import { Navigate, Outlet, type RouteObject } from "react-router";

import { AppShell } from "@/app/layouts/app-shell";
import { NotFoundPage } from "@/components/blocks/not-found-page";
import { OnboardingGate } from "@/components/shared/onboarding/onboarding-gate";
import {
  PublicOnly,
  RequireAdmin,
  RequireAuth,
} from "@/features/auth/components/guards";
import { RootLayout } from "@/app/root-layout";

// Each page is its own chunk, loaded the first time its route opens (web/ARCHITECTURE.md).
const SiteProfileRunPage = lazy(() =>
  import("@/features/apply/pages/site-profile-run-page").then((m) => ({
    default: m.SiteProfileRunPage,
  })),
);
const AccountPage = lazy(() =>
  import("@/features/account/pages/account-page").then((m) => ({
    default: m.AccountPage,
  })),
);
const AdminAgentPage = lazy(() =>
  import("@/features/admin/pages/agent-page").then((m) => ({
    default: m.AdminAgentPage,
  })),
);
const AdminAgentsPage = lazy(() =>
  import("@/features/admin/pages/agents-page").then((m) => ({
    default: m.AdminAgentsPage,
  })),
);
const AdminUsersPage = lazy(() =>
  import("@/features/admin/pages/users-page").then((m) => ({
    default: m.AdminUsersPage,
  })),
);
const RunPage = lazy(() =>
  import("@/features/agents/pages/runs-page").then((m) => ({
    default: m.RunPage,
  })),
);
const RunsPage = lazy(() =>
  import("@/features/agents/pages/runs-page").then((m) => ({
    default: m.RunsPage,
  })),
);
const QueuePage = lazy(() =>
  import("@/features/apply/pages/queue-page").then((m) => ({
    default: m.QueuePage,
  })),
);
const ApplicationsSettingsPage = lazy(() =>
  import("@/features/apply/pages/settings-page").then((m) => ({
    default: m.ApplicationsSettingsPage,
  })),
);
const SearchSettingsPage = lazy(() =>
  import("@/features/profile/pages/search-settings-page").then((m) => ({
    default: m.SearchSettingsPage,
  })),
);
const LinkPage = lazy(() =>
  import("@/features/auth/pages/link-page").then((m) => ({
    default: m.LinkPage,
  })),
);
const LoginPage = lazy(() =>
  import("@/features/auth/pages/login-page").then((m) => ({
    default: m.LoginPage,
  })),
);
const HomePage = lazy(() =>
  import("@/features/public/pages/home-page").then((m) => ({
    default: m.HomePage,
  })),
);
const PrivacyPage = lazy(() =>
  import("@/features/public/pages/privacy-page").then((m) => ({
    default: m.PrivacyPage,
  })),
);
const TermsPage = lazy(() =>
  import("@/features/public/pages/terms-page").then((m) => ({
    default: m.TermsPage,
  })),
);
const PostingDocumentPage = lazy(() =>
  import("@/features/documents/pages/posting-document-page").then((m) => ({
    default: m.PostingDocumentPage,
  })),
);
const TemplatesPage = lazy(() =>
  import("@/features/documents/pages/templates-page").then((m) => ({
    default: m.TemplatesPage,
  })),
);
const FillPage = lazy(() =>
  import("@/features/ingestion/pages/fill-page").then((m) => ({
    default: m.FillPage,
  })),
);
const JobDetailPage = lazy(() =>
  import("@/features/jobs/pages/job-detail-page").then((m) => ({
    default: m.JobDetailPage,
  })),
);
const JobsPage = lazy(() =>
  import("@/features/jobs/pages/jobs-page").then((m) => ({
    default: m.JobsPage,
  })),
);
const JobsMapPage = lazy(() =>
  import("@/features/jobs/pages/jobs-map-page").then((m) => ({
    default: m.JobsMapPage,
  })),
);
const DeckPage = lazy(() =>
  import("@/features/opportunities/pages/deck-page").then((m) => ({
    default: m.DeckPage,
  })),
);
const WelcomePage = lazy(() =>
  import("@/features/onboarding/pages/welcome-page").then((m) => ({
    default: m.WelcomePage,
  })),
);
const ProfilePage = lazy(() =>
  import("@/features/profile/pages/profile-page").then((m) => ({
    default: m.ProfilePage,
  })),
);
const CvPage = lazy(() =>
  import("@/features/profile/pages/cv-page").then((m) => ({
    default: m.CvPage,
  })),
);

/**
 * Every route of the app. Public: /, /privacy, /terms, /login, /invite/:token, /reset/:token.
 * Everything else sits behind RequireAuth, so an unknown path asks for a session before it says
 * "not found". /privacy and /terms make no api call at all; / checks the session once, to send a
 * signed in visitor to /jobs.
 * The shared providers and the scroll position are RootLayout's (root-layout.tsx).
 * The onboarding gate sits inside the shell: while it checks, the header and the footer show
 * around a skeleton.
 */
export const routes: RouteObject[] = [
  {
    element: <RootLayout />,
    children: [
      { path: "/", element: <HomePage /> },
      { path: "/privacy", element: <PrivacyPage /> },
      { path: "/terms", element: <TermsPage /> },
      {
        path: "/login",
        element: (
          <PublicOnly>
            <LoginPage />
          </PublicOnly>
        ),
      },
      { path: "/invite/:token", element: <LinkPage mode="invite" /> },
      { path: "/reset/:token", element: <LinkPage mode="reset" /> },
      {
        element: (
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        ),
        children: [
          {
            element: (
              <OnboardingGate>
                <Outlet />
              </OnboardingGate>
            ),
            children: [
              { path: "jobs", element: <JobsPage /> },
              { path: "jobs/map", element: <JobsMapPage /> },
              { path: "jobs/:id", element: <JobDetailPage /> },
              {
                path: "jobs/:id/documents/:kind",
                element: <PostingDocumentPage />,
              },
              { path: "discover", element: <DeckPage /> },
              { path: "welcome", element: <WelcomePage /> },
              { path: "documents/templates", element: <TemplatesPage /> },
              { path: "site-profiles/:id", element: <SiteProfileRunPage /> },
              {
                path: "account",
                element: <AccountPage />,
              },
              { path: "profile", element: <ProfilePage /> },
              {
                path: "profile/cv",
                element: <Navigate to="/profile#your-cv" replace />,
              },
              { path: "profile/cv/:id", element: <CvPage /> },
              { path: "profile/fill", element: <FillPage /> },
              { path: "runs", element: <RunsPage /> },
              { path: "runs/:id", element: <RunPage /> },
              { path: "applications", element: <QueuePage /> },
              { path: "settings/search", element: <SearchSettingsPage /> },
              {
                path: "settings/applications",
                element: <ApplicationsSettingsPage />,
              },
              {
                path: "admin/users",
                element: (
                  <RequireAdmin>
                    <AdminUsersPage />
                  </RequireAdmin>
                ),
              },
              {
                path: "admin/agents",
                element: (
                  <RequireAdmin>
                    <AdminAgentsPage />
                  </RequireAdmin>
                ),
              },
              {
                path: "admin/agents/:name",
                element: (
                  <RequireAdmin>
                    <AdminAgentPage />
                  </RequireAdmin>
                ),
              },
              { path: "*", element: <NotFoundPage /> },
            ],
          },
        ],
      },
    ],
  },
];
