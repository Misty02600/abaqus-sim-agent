import { useId, useSyncExternalStore } from "react";
import { SidePanel } from "./SidePanel";
import { getAppearance, setAppearance, subscribeAppearance, type Appearance } from "./appearance";

export function SettingsPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const sectionId = useId();
  const appearance = useSyncExternalStore(subscribeAppearance, getAppearance);
  if (!open) return null;
  return <SidePanel title="设置" onClose={onClose}>
    <nav className="settings-tabs" aria-label="设置分类">
      <button type="button" aria-current="page" aria-controls={`${sectionId}-general`}>常规</button>
    </nav>
    <section id={`${sectionId}-general`} className="general-settings">
      <h3>常规</h3>
      <label>外观
        <select aria-label="外观" value={appearance} onChange={event => setAppearance(event.target.value as Appearance)}>
          <option value="light">浅色</option>
          <option value="dark">深色</option>
          <option value="system">跟随系统</option>
        </select>
      </label>
    </section>
  </SidePanel>;
}
