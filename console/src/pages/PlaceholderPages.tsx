import { Link } from "react-router";
import { EmptyState, Section } from "../components/Section";

/**
 * ADR-037 information architecture, milestone M1: the global navigation is
 * complete, but two areas have no data source yet. They say so honestly
 * instead of pretending — an empty inbox is not "all clear".
 */
export function AttentionPage() {
  return (
    <Section
      title="Требует внимания"
      description="Inbox задач и продуктов, где нужен оператор: решения по гейтам, черновики брифов, ошибки репозиториев."
    >
      <EmptyState label="Inbox «Требует внимания» появится в M5 (T116)." />
      <p className="muted" style={{ marginBottom: 0 }}>
        Пока следующий шаг виден на странице каждого <Link to="/">продукта</Link> и задачи (блок «Следующий шаг»).
      </p>
    </Section>
  );
}

export function ActivityPage() {
  return (
    <Section title="Активность" description="Хронологическая лента событий фабрики по всем продуктам.">
      <EmptyState label="Лента активности вне объёма MVP." />
      <p className="muted" style={{ marginBottom: 0 }}>
        История по задаче доступна на её странице (прогоны, цепочка стадий); список — на странице{" "}
        <Link to="/">продуктов</Link>.
      </p>
    </Section>
  );
}

export function ServicePage() {
  return (
    <Section
      title="Служебное"
      description="Настройки контура и фабрики: не про продукты, а про то, как фабрика работает (ADR-037)."
    >
      <ul className="service-links" data-testid="service-links">
        <li>
          <Link to="/service/budgets">Бюджеты</Link> — настроенные лимиты и фактический расход по прогонам.
        </li>
        <li>
          <Link to="/service/ci">Этапы CI</Link> — переключатели этапов CI фабрики (ADR-026).
        </li>
        <li>
          <Link to="/service/settings">Настройки</Link> — токен оператора, адрес API, режим фабрики, профили ролей.
        </li>
        <li>
          <Link to="/changes">Все изменения</Link> — плоский список изменений без привязки к продукту (старый индекс).
        </li>
      </ul>
    </Section>
  );
}
