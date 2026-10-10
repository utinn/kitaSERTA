// Sidebar navigation. Items without `available: true` are rendered as
// disabled "Soon" entries until their feature is implemented.
export const NAV_SECTIONS = [
  {
    label: "Capture",
    items: [
      { id: "submit", label: "Submit Activity", icon: "upload", available: true },
      { id: "logs", label: "Activity Logs", icon: "list", available: true },
    ],
  },
  {
    label: "Automation",
    items: [
      { id: "candidates", label: "Automation Candidate", icon: "zap", available: true },
      { id: "audit", label: "Execution Audit", icon: "fileText", available: true },
    ],
  },
];
