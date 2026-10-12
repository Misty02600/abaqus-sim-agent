import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Icon } from "./Icon";
import { SidePanel } from "./SidePanel";

export const RIGHT_PANEL_MIN_WIDTH = 300;
const DEFAULT_WIDTH = 420;
const STORAGE_KEY = "abaqus-right-panel-width";

function initialWidth() {
  try {
    const value = Number(localStorage.getItem(STORAGE_KEY));
    if (Number.isFinite(value) && value >= RIGHT_PANEL_MIN_WIDTH) return value;
  } catch { /* Resizing also works without browser storage. */ }
  return DEFAULT_WIDTH;
}

export function RightPanel({ open, docked, maxWidth, title, onClose, children }: {
  open: boolean;
  docked: boolean;
  maxWidth: number;
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const titleId = useId();
  const body = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(initialWidth);
  const [resizing, setResizing] = useState(false);
  const drag = useRef<{ x: number; width: number } | null>(null);
  const limit = Math.max(RIGHT_PANEL_MIN_WIDTH, Math.min(760, maxWidth));
  const actualWidth = Math.max(RIGHT_PANEL_MIN_WIDTH, Math.min(limit, width));
  const liveWidth = useRef(actualWidth);
  liveWidth.current = actualWidth;

  function changeWidth(value: number, persist = true) {
    const next = Math.max(RIGHT_PANEL_MIN_WIDTH, Math.min(limit, value));
    liveWidth.current = next;
    setWidth(next);
    if (persist) {
      try { localStorage.setItem(STORAGE_KEY, String(next)); }
      catch { /* Browser storage is optional. */ }
    }
  }

  function finishResize() {
    if (!drag.current) return;
    drag.current = null;
    setResizing(false);
    changeWidth(liveWidth.current);
  }

  useEffect(() => {
    body.current?.scrollTo({ top: 0 });
  }, [title]);
  useEffect(() => {
    if (!open || !docked) {
      drag.current = null;
      setResizing(false);
    }
  }, [open, docked]);
  useEffect(() => {
    if (!resizing) return;
    document.body.classList.add("right-panel-resizing");
    return () => document.body.classList.remove("right-panel-resizing");
  }, [resizing]);

  if (!docked) {
    return open ? <SidePanel title={title} onClose={onClose}>{children}</SidePanel> : null;
  }
  return (
    <aside
      id="workbench-right-panel"
      className="right-panel"
      aria-labelledby={titleId}
      hidden={!open}
      style={{ width: actualWidth }}
      onKeyDown={event => {
        if (event.key === "Escape" && !event.defaultPrevented) {
          event.stopPropagation();
          onClose();
        }
      }}
    >
      <div
        className="right-panel-resizer"
        role="separator"
        tabIndex={0}
        aria-label="调整右侧栏宽度"
        aria-controls="workbench-right-panel"
        aria-orientation="vertical"
        aria-valuemin={RIGHT_PANEL_MIN_WIDTH}
        aria-valuemax={limit}
        aria-valuenow={actualWidth}
        onPointerDown={event => {
          if (event.button !== 0) return;
          event.preventDefault();
          event.currentTarget.focus();
          event.currentTarget.setPointerCapture(event.pointerId);
          drag.current = { x: event.clientX, width: actualWidth };
          setResizing(true);
        }}
        onPointerMove={event => {
          if (drag.current) changeWidth(drag.current.width + drag.current.x - event.clientX, false);
        }}
        onPointerUp={finishResize}
        onPointerCancel={finishResize}
        onLostPointerCapture={finishResize}
        onDoubleClick={() => changeWidth(DEFAULT_WIDTH)}
        onKeyDown={event => {
          const next = event.key === "ArrowLeft" ? actualWidth + 20
            : event.key === "ArrowRight" ? actualWidth - 20
            : event.key === "Home" ? RIGHT_PANEL_MIN_WIDTH
            : event.key === "End" ? limit : null;
          if (next === null) return;
          event.preventDefault();
          changeWidth(next);
        }}
      />
      <div className="side-panel-header">
        <h2 id={titleId} title={title}>{title}</h2>
        <button className="icon-button" aria-label="收起右侧栏" onClick={onClose}>
          <Icon name="close" />
        </button>
      </div>
      <div ref={body} className="side-panel-body">{children}</div>
    </aside>
  );
}
