export const EXECUTION_STATUS = {
  verified: { label: "Verified", tone: " status-success" },
  unverified: { label: "Unverified", tone: "" },
  unknown: { label: "Unknown", tone: " status-danger" },
  failed: { label: "Failed", tone: " status-danger" },
  claimed: { label: "In progress", tone: "" },
};
export const STATUS_ORDER = ["verified", "unverified", "unknown", "failed", "claimed"];

export function executionStatus(status) {
  return Object.hasOwn(EXECUTION_STATUS, status)
    ? EXECUTION_STATUS[status]
    : EXECUTION_STATUS.unknown;
}

export function countByStatus(records) {
  const counts = Object.fromEntries(STATUS_ORDER.map((status) => [status, 0]));
  for (const record of records) {
    const status = record?.status;
    counts[Object.hasOwn(EXECUTION_STATUS, status) ? status : "unknown"] += 1;
  }
  return counts;
}

export function formatDuration(seconds) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) return null;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${Math.round(seconds % 60)}s`;
}
