# ADR-010: Local Kubernetes with Helm and Argo CD

- **Статус**: принято
- **Дата**: 2026-09-13
- **Автор**: software-architect
- Решение согласовано пользователем (ответы на вопросы раздела 5 исторического реестра задач, 2026-09-13)
- **Уточнено** (2026-09-13, архитектурное ревью ADR-пакета): rollback при миграциях БД (п.6), разделение fallback RKE2/Compose (п.3), capacity smoke (п.2)

## Контекст

- Q-9 plan §5: локальный Docker Desktop Kubernetes + Helm + Argo CD на MacBook 24GB против VM (Compose/RKE2) и «K8S или VM».
- Влияние: лицензия Docker Desktop (корпоративное использование — проверка в bootstrap), ARM64-совместимость, воспроизводимость, запасной путь при нехватке ресурсов.

## Решение

1. Deployment-цель MVP — **локальный Docker Desktop Kubernetes на MacBook 24GB + Helm + Argo CD**; поток поставки — GitOps через `dark-factory-gitops` (ADR-015, T-043).
2. Профиль ресурсов — 10–12GB Docker, concurrency=1; namespaces, quotas, NetworkPolicy deny-by-default — T-040. В T-040 фиксируется capacity smoke полного контура (idle + один e2e job): peak RAM, CPU pressure, свободный диск; повышение concurrency — только при подтверждённом запасе.
3. **Лицензия Docker Desktop проверяется на bootstrap** (T-040); запасной путь — VM: RKE2 с теми же Helm chart'ами (values-VM); Compose — отдельный manifest без GitOps/Helm-parity, только деградированный режим без Argo CD. Argo CD к VM-командам произвольно не применяется.
4. ARM64: образы фабрики собираются под arm64/мульти-арх (T-041, T-044), digest'ы закрепляются.
5. Миграция в shared/DC-контур — T-091 (values-dc): тот же chart без изменений кода.
6. **Rollback и миграции данных.** Revert GitOps-коммита откатывает образ, но не схему БД. Изменения схемы (Alembic, T-006) — backward-compatible (expand/contract): в окне отката старый и новый образы совместимы со схемой; destructive-миграции — отдельным шагом после стабилизации. Исключения — отдельный migration/rollback plan в изменении.

Связанные задачи: T-040, T-042, T-043, T-091.

## Альтернативы

| Вариант | Плюсы | Минусы | Почему не выбран |
|---|---|---|---|
| Docker Desktop K8s + Helm + Argo CD | Нативный GitOps-поток; один chart для local/DC; зрелый ecosystem | Лицензия Docker Desktop; ресурсный потолок одного узла | Выбрано |
| VM (Compose/RKE2) | Ближе к prod-Linux, нет лицензионного вопроса | Тяжелее на MacBook, медленнее цикл, отдельная схема доставки | Запасной путь, не цель |
| «K8s или VM» | Гибкость | Не фиксирует цель — страдают воспроизводимость и шаблоны | Отклонено |

## Последствия

**Позитивные**
- Откат релиза — revert GitOps-коммита; то же дерево chart'ов переносится в DC (T-091).

**Негативные / риски**
- Одноузельность: HA вне MVP; нестабильность при исчерпании памяти — замер peak memory перед повышением concurrency (vision §6).
- Лицензионный риск Docker Desktop — закрыть проверкой в T-040 до начала эксплуатации.

**Дальше**
- T-040: воспроизводимый bootstrap + негативные тесты изоляции; T-042: chart + values-local; T-043: Argo CD Applications; T-091: миграция в DC.
