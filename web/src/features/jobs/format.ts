/** Dates and sizes as the jobs pages show them; the dates are the app's one format. */
export { formatDate, formatDateTime } from "@/lib/format";

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
