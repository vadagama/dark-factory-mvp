import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router";
import { createApiClient } from "../api/client";
import { POLL_MS, useAsync } from "../api/hooks";
import { ProductForm } from "../components/ProductForm";
import { EmptyState, ErrorState, LoadingState, Section } from "../components/Section";
import { StatusBadge } from "../components/StatusBadge";
import { TokenDialog } from "../components/TokenDialog";
import { formatDateTime, formatProduct, formatTime } from "../lib/format";
import { statusTone } from "../lib/statusTone";
import type { Change, Product } from "../api/types";

export interface ProductsModel {
  products: Product[];
  /** Number of changes per product id (changes without a product are not counted). */
  changesByProduct: Map<string, number>;
}

/** Groups the changes list by `product_id`; pre-T065 changes (null) are skipped. */
export function countChangesByProduct(changes: Change[]): Map<string, number> {
  const counts = new Map<string, number>();
  for (const change of changes) {
    if (change.product_id === null) {
      continue;
    }
    counts.set(change.product_id, (counts.get(change.product_id) ?? 0) + 1);
  }
  return counts;
}

/**
 * Index screen (ADR-037): the products of the factory with their readiness,
 * repository and number of changes, plus the registration form below.
 */
export function ProductsPage() {
  const api = useMemo(() => createApiClient(), []);
  const navigate = useNavigate();
  const [dialogOpen, setDialogOpen] = useState(false);

  const state = useAsync<ProductsModel>(
    async () => {
      // limit=200 is the API maximum; enough for the MVP list.
      const [products, changes] = await Promise.all([
        api.listProducts({ limit: 200 }),
        api.listChanges({ limit: 200 }),
      ]);
      return { products, changesByProduct: countChangesByProduct(changes) };
    },
    [],
    { pollMs: POLL_MS },
  );

  return (
    <>
      <Section
        title="Продукты"
        description="Продукт — верхний уровень работы фабрики (ADR-030): репозиторий, его готовность и задачи. Новая задача создаётся со страницы продукта."
      >
        {state.updatedAt !== null ? (
          <p className="muted" data-testid="updated-at">
            обновлено {formatTime(state.updatedAt)}
          </p>
        ) : null}
        {state.loading ? <LoadingState /> : null}
        {state.error ? <ErrorState message={state.error.detail} /> : null}
        {!state.loading && !state.error && state.data ? (
          state.data.products.length === 0 ? (
            <EmptyState label="Продуктов пока нет — зарегистрируйте первый продукт через форму ниже (нужен токен products:write)." />
          ) : (
            <table className="table" data-testid="products-table">
              <thead>
                <tr>
                  <th>Название</th>
                  <th>Статус</th>
                  <th>Репозиторий</th>
                  <th>Задач</th>
                  <th>Создан</th>
                </tr>
              </thead>
              <tbody>
                {state.data.products.map((product) => (
                  <tr key={product.id} data-testid={`product-row-${product.id}`}>
                    <td>
                      <Link to={`/products/${encodeURIComponent(product.id)}`}>{product.name}</Link>
                      <div className="mono muted">{product.id}</div>
                    </td>
                    <td>
                      <StatusBadge label={product.status} tone={statusTone(product.status, "product")} />
                      {product.status_reason ? <div className="muted">{product.status_reason}</div> : null}
                    </td>
                    <td className="mono">{formatProduct(product.repository.provider, product.repository.slug)}</td>
                    <td>{state.data?.changesByProduct.get(product.id) ?? 0}</td>
                    <td className="muted">{formatDateTime(product.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        ) : null}
      </Section>

      <ProductForm
        onCreated={(product) => navigate(`/products/${encodeURIComponent(product.id)}`)}
        onTokenRequired={() => setDialogOpen(true)}
      />
      <TokenDialog open={dialogOpen} onOpenChange={setDialogOpen} />
    </>
  );
}
