import * as Dialog from "@radix-ui/react-dialog";
import { useState } from "react";
import { setToken } from "../api/token";

interface TokenDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Shown after a failed mutation (e.g. 401) to explain why the token is needed. */
  reason?: string | null;
}

/**
 * Fail-closed UX (ADR-021 p.4): without a token mutating actions are
 * unavailable; a 401 opens this dialog. The input is masked (type=password),
 * the token goes to localStorage only and is never logged.
 */
export function TokenDialog({ open, onOpenChange, reason }: TokenDialogProps) {
  const [value, setValue] = useState("");
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog__overlay" />
        <Dialog.Content className="dialog">
          <Dialog.Title>Нужен токен оператора</Dialog.Title>
          <Dialog.Description className="dialog__description">
            {reason ?? "Мутации (intake, approvals) требуют Bearer-токен с нужным scope. Токен хранится только в localStorage этого браузера."}
          </Dialog.Description>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              const trimmed = value.trim();
              if (!trimmed) {
                return;
              }
              setToken(trimmed);
              setValue("");
              onOpenChange(false);
            }}
          >
            <label className="field">
              <span className="field__label">Токен</span>
              <input
                type="password"
                autoComplete="off"
                value={value}
                onChange={(event) => setValue(event.target.value)}
                placeholder="DARK_FACTORY_API_TOKENS"
              />
            </label>
            <div className="dialog__actions">
              <button type="submit" className="button button--primary" disabled={value.trim().length === 0}>
                Сохранить
              </button>
              <Dialog.Close asChild>
                <button type="button" className="button">
                  Отмена
                </button>
              </Dialog.Close>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
