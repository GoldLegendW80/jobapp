import { useEffect } from "react";

export const APP_NAME = "jobapp";

/** Sets the tab title to "<page> · jobapp" while the page is shown. */
export function useTitle(page: string) {
  useEffect(() => {
    document.title = page ? `${page} · ${APP_NAME}` : APP_NAME;
  }, [page]);
}
