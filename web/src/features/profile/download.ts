/** Save `document` as a JSON file through a temporary link. */
export function downloadJson(document_: unknown, fileName: string) {
  const blob = new Blob([`${JSON.stringify(document_, null, 2)}\n`], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  link.rel = "noopener";
  document.body.append(link);
  link.click();
  link.remove();
  // Give the browser time to start the download before the address goes.
  window.setTimeout(() => {
    URL.revokeObjectURL(url);
  }, 1000);
}
