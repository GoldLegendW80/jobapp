import { SiteProfileRunPage } from "@/features/apply/site-profile/run-page";

import { Outlet, ScrollRestoration, type RouteObject } from "react-router";

import { Toaster } from "@/components/ui/sonner";
import { AccountPage } from "@/features/account/account-page";
import { AdminUsersPage } from "@/features/admin/users-page";
import { RunPage, RunsPage } from "@/features/agents/runs-page";
import { QueuePage } from "@/features/apply/queue-page";
import { ApplicationsSettingsPage } from "@/features/apply/settings-page";
import { AuthProvider } from "@/features/auth/auth-provider";
import { PublicOnly, RequireAdmin, RequireAuth } from "@/features/auth/guards";
import { LinkPage } from "@/features/auth/link-page";
import { LoginPage } from "@/features/auth/login-page";
import { HomePage } from "@/features/public/home-page";
import { PrivacyPage } from "@/features/public/privacy-page";
import { TermsPage } from "@/features/public/terms-page";
import { PostingDocumentPage } from "@/features/documents/posting-document-page";
import { TemplatesPage } from "@/features/documents/templates-page";
import { FillPage } from "@/features/ingestion/fill-page";
import { JobDetailPage } from "@/features/jobs/job-detail-page";
import { JobsPage } from "@/features/jobs/jobs-page";
import { JobsMapPage } from "@/features/jobs/map/jobs-map-page";
import { DeckPage } from "@/features/opportunities/deck/deck-page";
import { OnboardingGate } from "@/features/onboarding/onboarding-gate";
import { WelcomePage } from "@/features/onboarding/welcome-page";
import { ProfilePage } from "@/features/profile/profile-page";
import { AppShell } from "@/features/shell/app-shell";
import { NotFoundPage } from "@/features/shell/not-found-page";

/**
 * Every route of the app. Public: /, /privacy, /terms, /login, /invite/:token, /reset/:token.
 * Everything else sits behind RequireAuth, so an unknown path asks for a session before it says
 * "not found". /privacy and /terms make no api call at all; / checks the session once, to send a
 * signed in visitor to /jobs.
 *
 * Scroll: a new page opens at its top, and Back (or a link) to a page seen before returns to
 * where it was left, so a posting opened from a long Jobs list comes back to the same row. The
 * position is kept per path, so a change of the filters in the address keeps it too.
 * The onboarding gate sits inside the shell: while it checks, the header and the footer show
 * around a skeleton.
 */
export const routes: RouteObject[] = [
  {
    element: (
      <AuthProvider>
        <Outlet />
        {/* On a phone, above the shell's bottom tab bar (shell/bottom-nav.tsx). */}
        <Toaster
          position="bottom-center"
          mobileOffset={{
            bottom: "calc(4.5rem + env(safe-area-inset-bottom))",
          }}
        />
        <ScrollRestoration getKey={(location) => location.pathname} />
      </AuthProvider>
    ),
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
              { path: "profile/cv/:id?", element: <ProfilePage /> },
              { path: "profile/fill", element: <FillPage /> },
              { path: "runs", element: <RunsPage /> },
              { path: "runs/:id", element: <RunPage /> },
              { path: "applications", element: <QueuePage /> },
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
              { path: "*", element: <NotFoundPage /> },
            ],
          },
        ],
      },
    ],
  },
];
