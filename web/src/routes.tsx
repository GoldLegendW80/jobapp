import { Outlet, type RouteObject } from "react-router";

import { Toaster } from "@/components/ui/sonner";
import { AccountPage } from "@/features/account/account-page";
import { AdminUsersPage } from "@/features/admin/users-page";
import { RunPage, RunsPage } from "@/features/agents/runs-page";
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
import { ProfilePage } from "@/features/profile/profile-page";
import { AppShell } from "@/features/shell/app-shell";
import { NotFoundPage } from "@/features/shell/not-found-page";

/**
 * Every route of the app. Public: /, /privacy, /terms, /login, /invite/:token, /reset/:token.
 * Everything else sits behind RequireAuth, so an unknown path asks for a session before it says
 * "not found". /privacy and /terms make no api call at all; / checks the session once, to send a
 * signed in visitor to /jobs.
 */
export const routes: RouteObject[] = [
  {
    element: (
      <AuthProvider>
        <Outlet />
        <Toaster position="bottom-center" />
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
          { path: "jobs", element: <JobsPage /> },
          { path: "jobs/map", element: <JobsMapPage /> },
          { path: "jobs/:id", element: <JobDetailPage /> },
          {
            path: "jobs/:id/documents/:kind",
            element: <PostingDocumentPage />,
          },
          { path: "documents/templates", element: <TemplatesPage /> },
          { path: "account", element: <AccountPage /> },
          { path: "profile", element: <ProfilePage /> },
          { path: "profile/cv/:id?", element: <ProfilePage /> },
          { path: "profile/fill", element: <FillPage /> },
          { path: "runs", element: <RunsPage /> },
          { path: "runs/:id", element: <RunPage /> },
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
];
