import Icon from "./Icon.jsx";
import { NAV_SECTIONS } from "../config/navigation.js";

export default function Sidebar({ activeId, onNavigate, open, onClose }) {
  return (
    <>
      <div
        className={`sidebar-backdrop${open ? " is-open" : ""}`}
        onClick={onClose}
        aria-hidden="true"
      />

      <aside
        id="app-sidebar"
        className={`sidebar${open ? " is-open" : ""}`}
        aria-label="Primary"
      >
        <div className="sidebar-brand">
          <span className="brand-mark" aria-hidden="true">
            <Icon name="logo" size={16} strokeWidth="2.25" />
          </span>
          <span className="brand-name">kitaSERTA</span>
          <button
            type="button"
            className="icon-button sidebar-close"
            onClick={onClose}
            aria-label="Close navigation"
          >
            <Icon name="close" />
          </button>
        </div>

        <nav className="sidebar-nav">
          {NAV_SECTIONS.map((section) => (
            <div className="nav-section" key={section.label}>
              <p className="nav-section-label">{section.label}</p>
              <ul>
                {section.items.map((item) => {
                  const isActive = item.id === activeId;

                  return (
                    <li key={item.id}>
                      <button
                        type="button"
                        className={`nav-item${isActive ? " is-active" : ""}`}
                        aria-current={isActive ? "page" : undefined}
                        disabled={!item.available}
                        title={item.available ? undefined : "Not available yet"}
                        onClick={() => onNavigate(item.id)}
                      >
                        <Icon name={item.icon} />
                        <span className="nav-item-label">{item.label}</span>
                        {!item.available && <span className="badge">Soon</span>}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </nav>

        <div className="sidebar-footer">
          <p className="sidebar-footer-title">Creator Dashboard</p>
          <p className="sidebar-footer-meta">Prototype · v0.1.0</p>
        </div>
      </aside>
    </>
  );
}
