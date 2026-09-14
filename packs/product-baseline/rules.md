# Правила пака

## Минимальные изменения (конституция, принцип II)

- Изменения ограничены требованиями задачи: SOLID/DRY/KISS/YAGNI, без попутных рефакторингов (`.specify/memory/constitution.md`, принцип II).
- ChangeSet — дельта `add/modify/supersede/retire` относительно baseline, а не копия спецификации (`docs/sdd-native-core.md` §9).
- Baseline содержит только принятые состояния: `active`, `superseded`, `retired` (§4); draft и proposed-артефакты живут внутри ChangeSet и попадают в baseline только после acceptance и reconciliation (§14).
- Прямые правки baseline мимо ChangeSet запрещены: любое изменение baseline проходит полный lifecycle ChangeSet — specify → design → tasks → verify → acceptance → reconciliation.

## Evidence-based validation (конституция, принцип III)

- Каждое изменение валидируется фактически: тесты плюс диагностика по изменённым файлам; заявлять о пройденной проверке, которая не запускалась, запрещено (`.specify/memory/constitution.md`, принцип III — non-negotiable).
- Ссылки на фактические доказательства (отчёты тестов, диагностика) фиксируются в `evidence/index.yaml` ChangeSet (§12); тяжёлые артефакты — в CI artifacts или Object Storage, в Git остаётся ссылка.
- Наличие артефакта не равно пройденному гейту: спецификационный гейт (T-021) вычисляет результат по нормализованному контракту ChangeSet и фиксирует `GateDecision` в `gates/specification.yaml` — политика и её версия, проверенная revision, результат, evidence, объяснение.
