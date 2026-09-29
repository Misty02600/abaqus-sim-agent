export function Icon({
  name,
}: {
  name:
    | "panel"
    | "search"
    | "folder"
    | "info"
    | "close"
    | "refresh"
    | "plus"
    | "trash"
    | "settings"
    | "chevron";
}) {
  const paths = {
    panel: "M4 4h16v16H4z M9 4v16",
    search: "M10.5 3.5a7 7 0 1 0 0 14 7 7 0 0 0 0-14 M16 16l5 5",
    folder: "M3 7h7l2 2h9v11H3z M3 7V4h7l2 3",
    info: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M12 11v6 M12 7v1",
    close: "M6 6l12 12 M6 18L18 6",
    refresh: "M20 10a8 8 0 1 0-1 8 M20 4v6h-6",
    plus: "M12 5v14 M5 12h14",
    trash: "M4 6h16 M9 6V3h6v3 M6 6l1 15h10l1-15 M10 10v7 M14 10v7",
    chevron: "M6 9l6 6 6-6",
    settings: "M4 6h4m4 0h8M8 3v6M4 12h10m4 0h2M14 9v6M4 18h4m4 0h8M8 15v6",
  };
  return (
    <svg
      aria-hidden="true"
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={paths[name]} />
    </svg>
  );
}
