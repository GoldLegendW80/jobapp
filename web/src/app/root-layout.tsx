import { Suspense } from "react";
import { Outlet, ScrollRestoration } from "react-router";

import { PageLoading } from "@/components/blocks/page-status";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AuthProvider } from "@/features/auth/components/auth-provider";

/**
 * What every page shares: the session, one provider for every Tooltip, the toasts and the scroll
 * position. Scroll: a new page opens at its top, and Back to a page seen before returns to where
 * it was left, kept per path, so a change of the filters in the address keeps it too.
 */
export function RootLayout() {
  return (
    <AuthProvider>
      <TooltipProvider>
        <Suspense fallback={<PageLoading />}>
          <Outlet />
        </Suspense>
        {/* On a phone, above the shell's bottom tab bar (layouts/bottom-nav.tsx). */}
        <Toaster
          position="bottom-center"
          mobileOffset={{
            bottom: "calc(4.5rem + env(safe-area-inset-bottom))",
          }}
        />
        <ScrollRestoration getKey={(location) => location.pathname} />
      </TooltipProvider>
    </AuthProvider>
  );
}
