# platform/ — желаемое состояние платформы (ns factory)

Плейсхолдер (T032): платформенное приложение `factory` сегодня разворачивается
Argo CD напрямую из репозитория платформы — source
`https://github.com/vadagama/dark-factory-mvp.git`, path `charts/dark-factory`,
values `values-local.yaml` (см. `deploy/argocd/chart/templates/applications.yaml`).

Когда T033 опубликует первый образ фабрики с immutable digest, сюда переедет
digest-привязка платформы в формате этого репозитория (двухисточниковая
Application: chart из `dark-factory-mvp` + values отсюда через `$values`),
чтобы обновление платформы шло тем же потоком «merge → GitOps MR с digest →
sync → откат revert-ом», что и для продуктов. До этого этот каталог не содержит
активных манифестов и не является источником истины.

Правила репозитория (README.md в корне) действуют здесь так же: только MR,
без секретов, только immutable digests.
