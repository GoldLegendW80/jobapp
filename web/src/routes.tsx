import { Navigate, Outlet, type RouteObject } from "react-router";

import { Toaster } from "@/components/ui/sonner";
import { AccountPage } from "@/features/account/account-page";
import { AdminUsersPage } from "@/features/admin/users-page";
import { AuthProvider } from "@/features/auth/auth-provider";
import { PublicOnly, RequireAdmin, RequireAuth } from "@/features/auth/guards";
import { LinkPage } from "@/features/auth/link-page";
import { LoginPage } from "@/features/auth/login-page";
import { JobDetailPage } from "@/features/jobs/job-detail-page";
import { JobsPage } from "@/features/jobs/jobs-page";
import { JobsMapPage } from "@/features/jobs/map/jobs-map-page";
import { ProfilePage } from "@/features/profile/profile-page";
import { AppShell } from "@/features/shell/app-shell";
import { NotFoundPage } from "@/features/shell/not-found-page";

/**
 * Every route of the app. Public: /login, /invite/:token, /reset/:token. Everything else sits
 * behind RequireAuth, so an unknown path asks for a session before it says "not found".
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
          // The jobs dashboard is the home page.
          { index: true, element: <Navigate to="/jobs" replace /> },
          { path: "jobs", element: <JobsPage /> },
          { path: "jobs/map", element: <JobsMapPage /> },
          { path: "jobs/:id", element: <JobDetailPage /> },
          { path: "account", element: <AccountPage /> },
          { path: "profile", element: <ProfilePage /> },
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
