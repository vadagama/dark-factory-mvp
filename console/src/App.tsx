import { Route, Routes } from "react-router";
import { Layout } from "./components/Layout";
import { BudgetsPage } from "./pages/BudgetsPage";
import { ChangeCardPage } from "./pages/ChangeCardPage";
import { ChangesListPage } from "./pages/ChangesListPage";
import { CiStagesPage } from "./pages/CiStagesPage";
import { GatesPage } from "./pages/GatesPage";
import { SettingsPage } from "./pages/SettingsPage";

/**
 * Routing: react-router v7 with browser-history routing (ADR-021 p.1). The
 * `vite preview` and the console nginx image both fall back to index.html,
 * so deep links (e.g. /changes/:id) work in dev, e2e and the chart.
 */
export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<ChangesListPage />} />
        <Route path="changes/:changeId" element={<ChangeCardPage />} />
        <Route path="changes/:changeId/gates" element={<GatesPage />} />
        <Route path="budgets" element={<BudgetsPage />} />
        <Route path="ci" element={<CiStagesPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<ChangesListPage />} />
      </Route>
    </Routes>
  );
}
