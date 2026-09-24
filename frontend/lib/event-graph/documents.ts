/** Human-readable size for an ingested document payload. */

export function formatPeso(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const digits = Number.isInteger(value) || value >= 10 ? 0 : 1;
  return `${value.toFixed(digits)} ${units[unit]}`;
}
