// Formatting helpers shared by candidate review and execution audit views.

export function humanize(value) {
  if (value === null || value === undefined || value === "") return null;
  const text = String(value).replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function asArray(value) {
  return Array.isArray(value) ? value : [];
}

// Steps may be plain strings or { step_number, action, tool } objects.
export function normalizeStep(step) {
  if (typeof step === "string") return { action: step };
  if (step && typeof step === "object") {
    return { action: step.action ?? step.description, tool: step.tool };
  }
  return { action: null };
}
