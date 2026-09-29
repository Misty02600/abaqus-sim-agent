export type Appearance = "light" | "dark" | "system";
const STORAGE_KEY = "abaqus-appearance";
const listeners = new Set<() => void>();
const systemTheme = window.matchMedia("(prefers-color-scheme: dark)");

function parse(value: string | null): Appearance {
  return value === "light" || value === "dark" ? value : "system";
}

function load(): Appearance {
  try { return parse(localStorage.getItem(STORAGE_KEY)); }
  catch { return "system"; }
}

let appearance = load();

function apply() {
  document.documentElement.dataset.theme = appearance === "system"
    ? (systemTheme.matches ? "dark" : "light") : appearance;
}

export function initializeAppearance() {
  apply();
  systemTheme.addEventListener("change", () => {
    if (appearance === "system") apply();
  });
  window.addEventListener("storage", event => {
    if (event.key !== STORAGE_KEY && event.key !== null) return;
    appearance = parse(event.newValue);
    apply();
    listeners.forEach(listener => listener());
  });
}

export const getAppearance = () => appearance;

export function subscribeAppearance(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function setAppearance(value: Appearance) {
  appearance = value;
  try { localStorage.setItem(STORAGE_KEY, value); } catch { /* Current-page choice still works. */ }
  apply();
  listeners.forEach(listener => listener());
}
