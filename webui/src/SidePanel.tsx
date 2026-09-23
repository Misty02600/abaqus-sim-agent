import { useEffect, useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Icon } from "./Icon";

export function SidePanel({
  title,
  onClose,
  children,
  closeDisabled = false,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  closeDisabled?: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  // File panels can be opened from the sidebar. Keep the modal outside that
  // subtree so a responsive sidebar collapse cannot hide an open dialog.
  return createPortal(
    <dialog
      ref={dialog}
      className="side-panel"
      aria-labelledby={titleId}
      onCancel={(event) => {
        if (closeDisabled) event.preventDefault();
        else onClose();
      }}
      onClick={(event) => {
        if (closeDisabled || event.target !== event.currentTarget) return;
        const bounds = event.currentTarget.getBoundingClientRect();
        if (
          event.clientX < bounds.left ||
          event.clientX > bounds.right ||
          event.clientY < bounds.top ||
          event.clientY > bounds.bottom
        )
          onClose();
      }}
    >
      <div className="side-panel-header">
        <h2 id={titleId}>{title}</h2>
        <button
          className="icon-button"
          aria-label="关闭面板"
          onClick={onClose}
          disabled={closeDisabled}
        >
          <Icon name="close" />
        </button>
      </div>
      <div className="side-panel-body">{children}</div>
    </dialog>,
    document.body,
  );
}
