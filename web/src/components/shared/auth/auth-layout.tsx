import type { ReactNode } from "react";

import { Brand } from "@/components/blocks/brand";

/** Frame of the sign in pages: the app's mark, a link home, above one centred card. */
export function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col items-center px-4 py-10 sm:py-16">
      <Brand to="/" className="mb-6 sm:mb-8" />
      <main className="w-full max-w-sm">{children}</main>
    </div>
  );
}
