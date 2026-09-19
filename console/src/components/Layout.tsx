import { NavLink, Outlet } from "react-router";
import { meta, factoryModeLabel } from "../lib/meta";

/**
 * App shell (ADR-037 IA): four top-level areas — products are the index,
 * «Требует внимания» and «Активность» are honest placeholders in M1, the
 * service area groups budgets, CI stages and settings.
 */
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
          Продукты
        </NavLink>
        <NavLink to="/attention">Требует внимания</NavLink>
        <NavLink to="/activity">Активность</NavLink>
        <NavLink to="/service">Служебное</NavLink>
      </nav>
      <main className="layout__main">
        <Outlet />
      </main>
      <footer className="layout__footer">
        <span>dark-factory-mvp · T036 · ADR-021 · ADR-037</span>
      </footer>
    </div>
  );
}
