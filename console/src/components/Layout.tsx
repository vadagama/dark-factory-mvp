import { NavLink, Outlet } from "react-router";
import { meta, factoryModeLabel } from "../lib/meta";

/** App shell: one nav for every screen, mode badge from the snapshot. */
export function Layout() {
  const mode = meta.factory_mode;
  return (
    <div className="layout">
      <header className="layout__header">
        <span className="layout__brand">Dark Factory Console</span>
        <span className={`badge badge--${mode.human_gates.length > 0 ? "warning" : "success"}`} title="Режим фабрики (информационный; переключение вне скоупа MVP)">
          {factoryModeLabel(mode.mode)}
        </span>
      </header>
      <nav className="layout__nav" aria-label="Основная навигация">
        <NavLink to="/" end>
          Изменения
        </NavLink>
        <NavLink to="/budgets">Бюджеты</NavLink>
        <NavLink to="/ci">Этапы CI</NavLink>
        <NavLink to="/settings">Настройки</NavLink>
      </nav>
      <main className="layout__main">
        <Outlet />
      </main>
      <footer className="layout__footer">
        <span>dark-factory-mvp · T036 · ADR-021</span>
      </footer>
    </div>
  );
}
