import { useCallback, useEffect, useId, useState, useSyncExternalStore } from "react";
import { errorText, request } from "./api/client";
import type { components } from "./api/schema";
import { SidePanel } from "./SidePanel";
import { getAppearance, setAppearance, subscribeAppearance, type Appearance } from "./appearance";

type Provider = components["schemas"]["ProviderOption"];
type Login = components["schemas"]["LoginStatus"];

export type SettingsSection = "general" | "models";

export function SettingsPanel({ open, ready, busy, section, onSectionChange, onClose, onChanged }: {
  open: boolean; ready: boolean; busy: boolean; onClose: () => void; onChanged: () => void;
  section: SettingsSection; onSectionChange: (section: SettingsSection) => void;
}) {
  const sectionId = useId();
  const appearance = useSyncExternalStore(subscribeAppearance, getAppearance);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [configured, setConfigured] = useState<string[]>([]);
  const [provider, setProvider] = useState("");
  const [dependenciesReady, setDependenciesReady] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [clientId, setClientId] = useState("");
  const [message, setMessage] = useState("");
  const [working, setWorking] = useState(false);
  const [login, setLogin] = useState<Login | null>(null);
  const selected = providers.find(item => item.id === provider);
  const waiting = login?.state === "waiting";
  const refreshConnections = useCallback(async () => {
    setConfigured(await request<string[]>("/model-connections"));
  }, []);

  useEffect(() => {
    if (!open) { setApiKey(""); return; }
    if (!ready) return;
    let cancelled = false;
    Promise.all([request<Provider[]>("/model-providers"), request<string[]>("/model-connections")])
      .then(([items, connected]) => { if (!cancelled) { setProviders(items); setConfigured(connected); } })
      .catch(error => { if (!cancelled) setMessage(errorText(error)); });
    return () => { cancelled = true; };
  }, [open, ready]);

  useEffect(() => {
    setDependenciesReady(false); setApiKey(""); setClientId(""); setMessage(""); setLogin(null);
    if (!ready || !provider) return;
    let cancelled = false;
    request(`/model-provider-dependencies?provider=${encodeURIComponent(provider)}`)
      .then(() => { if (!cancelled) setDependenciesReady(true); })
      .catch(error => { if (!cancelled) setMessage(errorText(error)); });
    return () => { cancelled = true; };
  }, [ready, provider]);

  // Keep observing an active login even when the settings panel is closed.
  useEffect(() => {
    if (!login || login.state !== "waiting") return;
    let cancelled = false;
    const timer = window.setInterval(() => {
      request<Login>(`/model-logins/${login.id}`).then(async next => {
        if (cancelled) return;
        setLogin(next);
        if (next.state === "completed") {
          onChanged();
          setMessage("登录已保存，可以返回选择模型。");
          await refreshConnections();
        } else if (next.state === "failed") setMessage(next.error || "登录失败。");
      }).catch(error => { if (!cancelled) setMessage(errorText(error)); });
    }, 1500);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [login, onChanged, refreshConnections]);

  async function saveKey() {
    setWorking(true);
    try {
      await request("/model-connections", { provider, api_key: apiKey });
      setApiKey(""); onChanged();
      setMessage("API Key 已保存，CLI 和网页共用。");
      await refreshConnections();
    } catch (error) { setMessage(errorText(error)); }
    finally { setWorking(false); }
  }

  async function beginLogin() {
    setWorking(true);
    try {
      const next = await request<Login>("/model-logins", { provider, ...(clientId ? { client_id: clientId } : {}) });
      setLogin(next); setMessage("请打开登录页面完成授权。");
    } catch (error) { setMessage(errorText(error)); }
    finally { setWorking(false); }
  }

  async function removeSaved() {
    setWorking(true);
    try {
      await request(`/model-connections/${encodeURIComponent(provider)}`, {}, "DELETE");
      onChanged(); await refreshConnections();
      setMessage("已移除本应用保存的凭据；环境变量和其他应用登录不受影响。");
    } catch (error) { setMessage(errorText(error)); }
    finally { setWorking(false); }
  }

  if (!open) return null;
  return <SidePanel title="设置" onClose={onClose}>
    <nav className="settings-tabs" aria-label="设置分类">
      <button type="button" aria-current={section === "general" ? "page" : undefined} aria-controls={`${sectionId}-general`} onClick={() => onSectionChange("general")}>常规</button>
      <button type="button" aria-current={section === "models" ? "page" : undefined} aria-controls={`${sectionId}-models`} onClick={() => onSectionChange("models")}>模型账号</button>
    </nav>
    <section id={`${sectionId}-general`} className="general-settings" hidden={section !== "general"}>
      <h3>常规</h3>
      <label>外观
        <select aria-label="外观" value={appearance} onChange={event => setAppearance(event.target.value as Appearance)}>
          <option value="light">浅色</option>
          <option value="dark">深色</option>
          <option value="system">跟随系统</option>
        </select>
      </label>
    </section>
    <section id={`${sectionId}-models`} className="model-account-settings" hidden={section !== "models"}>
      <div><h3>模型账号</h3><p className="muted small">在这里登录或保存 API Key，然后返回对话选择模型。</p></div>
      <label>提供商
        <select value={provider} disabled={!ready || busy || working || waiting} onChange={event => setProvider(event.target.value)}>
          <option value="">选择需要设置的提供商</option>
          {providers.map(item => <option key={item.id} value={item.id}>{item.label}{configured.includes(item.id) ? " · 已配置" : ""}</option>)}
        </select>
      </label>
      {selected && dependenciesReady && <>
        {selected.api_key_environment && <div className="row">
          <label>API Key<input type="password" value={apiKey} autoComplete="off" disabled={busy || working || waiting} onChange={event => setApiKey(event.target.value)} placeholder={configured.includes(provider) ? "输入新的 Key 可更新" : "输入 API Key"} /></label>
          <button type="button" disabled={busy || working || waiting || !apiKey.trim()} onClick={() => void saveKey()}>保存 API Key</button>
        </div>}
        {selected.login && <>
          {selected.login === "github-copilot" && <label>GitHub OAuth Client ID<input value={clientId} disabled={working || waiting} onChange={event => setClientId(event.target.value)} /></label>}
          <div><button type="button" disabled={busy || working || waiting} onClick={() => void beginLogin()}>登录账号</button></div>
        </>}
        {selected.local_auth && <p className="muted small">也可使用提供商支持的本机认证来源。</p>}
        {waiting && login && <div className="row"><a href={login.url} target="_blank" rel="noreferrer">打开登录页面</a>{login.user_code && <code>{login.user_code}</code>}
          <button type="button" onClick={() => { void request(`/model-logins/${login.id}`, {}, "DELETE").then(() => { setLogin(null); setMessage("登录已取消。"); }).catch(error => setMessage(errorText(error))); }}>取消登录</button>
        </div>}
        {configured.includes(provider) && <div><button type="button" className="subtle" disabled={busy || working || waiting} onClick={() => void removeSaved()}>移除本应用保存的凭据</button></div>}
      </>}
      {message && <p role="status" className="muted small">{message}</p>}
    </section>
  </SidePanel>;
}
