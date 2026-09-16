import { useEffect, useId, useRef, type ReactNode } from "react";
import { Icon } from "./Icon";

export function SidePanel({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  return (
    <dialog
      ref={dialog}
      className="side-panel"
      aria-labelledby={titleId}
      onCancel={onClose}
      onClick={(event) => {
        if (event.target !== event.currentTarget) return;
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
        <button className="icon-button" aria-label="关闭面板" onClick={onClose}>
          <Icon name="close" />
        </button>
      </div>
      <div className="side-panel-body">{children}</div>
    </dialog>
  );
}
