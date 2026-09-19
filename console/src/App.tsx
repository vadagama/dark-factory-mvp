import { Navigate, Route, Routes } from "react-router";
import { Layout } from "./components/Layout";
import { BudgetsPage } from "./pages/BudgetsPage";
import { ChangeCardPage } from "./pages/ChangeCardPage";
import { ChangeSetPage } from "./pages/ChangeSetPage";
import { ChangesListPage } from "./pages/ChangesListPage";
import { CiStagesPage } from "./pages/CiStagesPage";
import { GatesPage } from "./pages/GatesPage";
import { IntakePage } from "./pages/IntakePage";
import { ActivityPage, AttentionPage, ServicePage } from "./pages/PlaceholderPages";
import { ProductPage } from "./pages/ProductPage";
import { ProductsPage } from "./pages/ProductsPage";
import { SettingsPage } from "./pages/SettingsPage";

/**
 * Routing: react-router v7 with browser-history routing (ADR-021 p.1). The
 * `vite preview` and the console nginx image both fall back to index.html,
 * so deep links (e.g. /changes/:id) work in dev, e2e and the chart.
 *
 * ADR-037 IA (M1): products are the index; the service screens moved under
 * /service and the old paths redirect so bookmarks keep working.
 * M2 (T088): /changes/:id is the ChangeSet workspace; the M1 change card
 * stays reachable at /changes/:id/card.
 */
export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<ProductsPage />} />
        <Route path="products/:productId" element={<ProductPage />} />
        <Route path="products/:productId/new-change" element={<IntakePage />} />
        <Route path="changes" element={<ChangesListPage />} />
        <Route path="changes/:changeId" element={<ChangeSetPage />} />
        <Route path="changes/:changeId/card" element={<ChangeCardPage />} />
        <Route path="changes/:changeId/gates" element={<GatesPage />} />
        <Route path="attention" element={<AttentionPage />} />
        <Route path="activity" element={<ActivityPage />} />
        <Route path="service" element={<ServicePage />} />
        <Route path="service/budgets" element={<BudgetsPage />} />
        <Route path="service/ci" element={<CiStagesPage />} />
        <Route path="service/settings" element={<SettingsPage />} />
        <Route path="budgets" element={<Navigate to="/service/budgets" replace />} />
        <Route path="ci" element={<Navigate to="/service/ci" replace />} />
        <Route path="settings" element={<Navigate to="/service/settings" replace />} />
        <Route path="*" element={<ProductsPage />} />
      </Route>
    </Routes>
  );
}
