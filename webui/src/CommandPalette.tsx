import { useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "./Icon";

export type QuickAction = {
  id: string;
  label: string;
  keywords?: string;
  shortcut?: string;
  disabled?: boolean;
  hint?: string;
  run: () => void | Promise<void>;
};
export function CommandPalette({
  actions,
  onClose,
  onError,
}: {
  actions: QuickAction[];
  onClose: () => void;
  onError: (error: unknown) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const filtered = useMemo(
    () =>
      actions.filter((action) =>
        `${action.label} ${action.keywords ?? ""}`
          .toLocaleLowerCase()
          .includes(query.toLocaleLowerCase()),
      ),
    [actions, query],
  );
  useEffect(() => {
    ref.current?.showModal();
    input.current?.focus();
  }, []);
  async function choose(action: QuickAction) {
    if (action.disabled) return;
    onClose();
    try {
      await action.run();
    } catch (error) {
      onError(error);
    }
  }
  return (
    <dialog
      ref={ref}
      className="command-palette"
      aria-label="快速操作"
      onCancel={onClose}
      onClick={(event) => {
        const bounds = event.currentTarget.getBoundingClientRect();
        if (
          event.target === event.currentTarget &&
          (event.clientX < bounds.left ||
            event.clientX > bounds.right ||
            event.clientY < bounds.top ||
            event.clientY > bounds.bottom)
        )
          onClose();
      }}
      onKeyDown={(event) => {
        if (
          !["ArrowDown", "ArrowUp", "Enter"].includes(event.key) ||
          event.nativeEvent.isComposing
        )
          return;
        const buttons = Array.from(
          ref.current?.querySelectorAll<HTMLButtonElement>(
            ".command-option:not(:disabled)",
          ) ?? [],
        );
        if (event.key === "Enter" && event.target === input.current) {
          event.preventDefault();
          buttons[0]?.click();
        }
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          const at = buttons.indexOf(
            document.activeElement as HTMLButtonElement,
          );
          const next =
            at < 0
              ? event.key === "ArrowDown"
                ? 0
                : buttons.length - 1
              : (at + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) %
                buttons.length;
          buttons[next]?.focus();
        }
      }}
    >
      <div className="command-search">
        <Icon name="search" />
        <input
          ref={input}
          aria-label="搜索操作"
          placeholder="搜索操作…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <kbd>Esc</kbd>
      </div>
      <div className="command-options">
        {filtered.map((action) => (
          <button
            key={action.id}
            className="command-option"
            disabled={action.disabled}
            onClick={() => void choose(action)}
          >
            <span>
              {action.label}
              {action.hint && <small>{action.hint}</small>}
            </span>
            {action.shortcut && <kbd>{action.shortcut}</kbd>}
          </button>
        ))}
        {!filtered.length && <p className="empty">没有匹配的操作</p>}
      </div>
    </dialog>
  );
}
