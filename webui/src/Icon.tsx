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
    chevron: "M6 9l6 6 6-6",
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
