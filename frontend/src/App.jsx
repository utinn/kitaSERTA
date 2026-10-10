import { useEffect, useState } from "react";
import "./App.css";
import Sidebar from "./components/Sidebar.jsx";
import Topbar from "./components/Topbar.jsx";
import ActivitySubmissionPage from "./components/ActivitySubmissionPage.jsx";
import ActivityLogs from "./components/ActivityLogs";
import AutomationCandidatePage from "./components/AutomationCandidatePage.jsx";
import ExecutionAuditPage from "./components/ExecutionAuditPage.jsx";

const PAGES = {
  submit: { title: "Submit Activity", Component: ActivitySubmissionPage },
  logs: { title: "Activity Logs", Component: ActivityLogs },
  candidates: {
    title: "Automation Candidate",
    Component: AutomationCandidatePage,
  },
  audit: { title: "Execution Audit", Component: ExecutionAuditPage },
};

export default function App() {
  const [activePage, setActivePage] = useState("submit");
  const [sidebarOpen, setSidebarOpen] = useState(false);

  useEffect(() => {
    if (!sidebarOpen) return undefined;

    function handleKeyDown(event) {
      if (event.key === "Escape") setSidebarOpen(false);
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [sidebarOpen]);

  function handleNavigate(id) {
    if (PAGES[id]) setActivePage(id);
    setSidebarOpen(false);
  }

  const { title, Component } = PAGES[activePage];

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>

      <Sidebar
        activeId={activePage}
        onNavigate={handleNavigate}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
      />

      <div className="app-main">
        <Topbar
          title={title}
          sidebarOpen={sidebarOpen}
          onMenuClick={() => setSidebarOpen(true)}
        />
        <main className="content" id="main-content" tabIndex={-1}>
          <Component />
        </main>
      </div>
    </div>
  );
}
