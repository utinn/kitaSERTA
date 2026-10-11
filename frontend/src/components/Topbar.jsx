import Icon from "./Icon.jsx";

export default function Topbar({ title, sidebarOpen, onMenuClick }) {
  return (
    <header className="topbar">
      <button
        type="button"
        className="icon-button topbar-menu"
        onClick={onMenuClick}
        aria-label="Open navigation"
        aria-controls="app-sidebar"
        aria-expanded={sidebarOpen}
      >
        <Icon name="menu" size={18} />
      </button>

      <nav className="breadcrumb" aria-label="Breadcrumb">
        <span className="breadcrumb-root">kitaSERTA</span>
        <span className="breadcrumb-sep" aria-hidden="true">/</span>
        <span className="breadcrumb-current" aria-current="page">
          {title}
        </span>
      </nav>
    </header>
  );
}
