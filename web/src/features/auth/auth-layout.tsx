import type { ReactNode } from "react";

import { APP_NAME } from "@/lib/use-title";

/** Frame of the public pages: the app name above one centred card. */
export function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col items-center px-4 py-10 sm:py-16">
      <p className="mb-6 text-lg font-semibold tracking-tight sm:mb-8">
        {APP_NAME}
      </p>
      <main className="w-full max-w-sm">{children}</main>
    </div>
  );
}
