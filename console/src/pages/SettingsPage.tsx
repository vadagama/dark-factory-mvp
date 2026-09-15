import { useState } from "react";
import type { FormEvent } from "react";
import { clearToken, getToken, maskToken, setToken } from "../api/token";
import { DEFAULT_API_BASE_URL, getApiBaseUrl, setApiBaseUrl } from "../api/settings";
import { Notice, Section } from "../components/Section";
import { factoryModeLabel, meta } from "../lib/meta";

/**
 * Screen 5 (ADR-021 p.4/p.5): local console settings (token, API URL) live in
 * localStorage; profiles and the factory mode are rendered from the
 * generated snapshot. Mode switching is informational only — there is no API
 * for it in the MVP (documented in console/README.md).
 */
export function SettingsPage() {
  const [tokenDraft, setTokenDraft] = useState("");
  const [saved, setSaved] = useState<string | null>(null);
  const [baseUrlDraft, setBaseUrlDraft] = useState(getApiBaseUrl());
  const [baseUrlSaved, setBaseUrlSaved] = useState(false);

  const current = getToken();

  const onSaveToken = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmed = tokenDraft.trim();
    if (!trimmed) {
      return;
    }
    setToken(trimmed);
    setTokenDraft("");
    setSaved("Токен сохранён (localStorage, только на этом устройстве).");
  };

  const onClearToken = () => {
    clearToken();
    setSaved("Токен удалён из localStorage.");
  };

  const onSaveBaseUrl = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setApiBaseUrl(baseUrlDraft);
    setBaseUrlDraft(getApiBaseUrl());
    setBaseUrlSaved(true);
  };

  const mode = meta.factory_mode;

  return (
    <>
      <Section
        title="Токен оператора"
        description="Bearer-токен для мутаций (changes:write, approvals:write). Хранится только в localStorage браузера; GET-запросы его не носят; никогда не попадает в логи и URL."
      >
        {current ? (
          <p data-testid="token-masked">
            Текущий токен: <code className="mono">{maskToken(current)}</code>{" "}
            <span className="muted">(маскирован, виден только суффикс)</span>
          </p>
        ) : (
          <p className="muted" data-testid="token-absent">
            Токен не задан — мутации (приёмка, согласования) недоступны.
          </p>
        )}
        {saved ? <Notice tone="success">{saved}</Notice> : null}
        <form onSubmit={onSaveToken} data-testid="token-form">
          <div className="field">
            <label className="field__label" htmlFor="token-input">
              Новый токен
            </label>
            <input
              id="token-input"
              type="password"
              autoComplete="off"
              placeholder="введите DARK_FACTORY_API_TOKENS значение"
              value={tokenDraft}
              onChange={(event) => setTokenDraft(event.target.value)}
            />
            <span className="field__hint">
              Ввод маскирован. Внимание: localStorage доступен любому скрипту этой страницы — используйте
              токен только на доверенном устройстве (SC-008: потеря кэша безопасна, токен вводится повторно).
            </span>
          </div>
          <div className="form-actions">
            <button type="submit" className="button button--primary" disabled={tokenDraft.trim().length === 0}>
              Сохранить токен
            </button>
            <button type="button" className="button button--danger" onClick={onClearToken} disabled={!current}>
              Удалить токен
            </button>
          </div>
        </form>
      </Section>

      <Section title="Адрес API" description="По умолчанию /api/v1 (same origin: vite-прокси в dev, nginx-прокси в chart). Менять нужно только для внешнего API.">
        <form onSubmit={onSaveBaseUrl} data-testid="baseurl-form">
          <div className="field">
            <label className="field__label" htmlFor="baseurl-input">
              Base URL
            </label>
            <input
              id="baseurl-input"
              type="text"
              value={baseUrlDraft}
              onChange={(event) => setBaseUrlDraft(event.target.value)}
            />
          </div>
          <div className="form-actions">
            <button type="submit" className="button">
              Сохранить
            </button>
            {baseUrlSaved ? <span className="muted">Сохранено (применится к новым запросам).</span> : null}
          </div>
        </form>
      </Section>

      <Section
        title="Режим фабрики"
        description="Информационный вывод из снапшота: наличие human-гейтов в маршруте и auto-merge риск-классов (ADR-018). Интерактивное переключение вне скоупа MVP — нет API."
      >
        <p data-testid="factory-mode">
          Текущий режим: <strong>{factoryModeLabel(mode.mode)}</strong>
        </p>
        <ul>
          <li>
            Human-гейты:{" "}
            {mode.human_gates.length === 0 ? (
              <span className="muted">нет</span>
            ) : (
              mode.human_gates.map((gate) => gate.replaceAll("_", " ")).join(", ")
            )}
          </li>
          <li>
            Auto-merge риск-классы:{" "}
            {mode.auto_merge_risk_classes.length === 0 ? (
              <span className="muted">нет (merge — только человек, ADR-011)</span>
            ) : (
              mode.auto_merge_risk_classes.join(", ")
            )}
          </li>
          <li>
            Гейт согласования merge: <span className="mono">{mode.merge_authorization_gate}</span> · метод:{" "}
            {mode.merge_methods.join(", ")}
          </li>
        </ul>
      </Section>

      <Section
        title="Профили ролей"
        description="Из снапшота (src/dark_factory/agents/profiles → meta.json). Ядро MVP: product, develop, quality (ADR-007 п.4)."
      >
        <div className="stack" data-testid="profiles-list">
          {meta.profiles.map((profile) => (
            <details key={profile.role} className="profile">
              <summary>
                {profile.name} <span className="mono">({profile.role}, v{profile.version})</span>
              </summary>
              <p>{profile.description}</p>
              <p className="muted">Входы: {profile.inputs.join(", ")}</p>
              <p className="muted">Выходы: {profile.outputs.join(", ")}</p>
              <p className="muted">Инструменты: {profile.tools.join(", ")}</p>
              <p className="muted">Навыки: {profile.skills.join(", ")}</p>
              <p>
                <strong>Ограничения:</strong>
              </p>
              <ul>
                {profile.constraints.map((constraint) => (
                  <li key={constraint}>{constraint}</li>
                ))}
              </ul>
              <p>
                <strong>Условия остановки:</strong>
              </p>
              <ul>
                {profile.stop_conditions.map((condition) => (
                  <li key={condition}>{condition}</li>
                ))}
              </ul>
            </details>
          ))}
        </div>
        <p className="muted" style={{ marginBottom: 0 }}>
          Базовый URL по умолчанию: <span className="mono">{DEFAULT_API_BASE_URL}</span>
        </p>
      </Section>
    </>
  );
}
