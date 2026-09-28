import { useEffect, useState } from "react";
import { request, errorText } from "./api/client";

type Provider = { id: string; label: string; models: string[]; api_key_environment: string | null; local_auth: boolean; login: string | null };
type Login = { id: string; state: string; url: string; user_code?: string | null; error?: string | null };
export type ModelSelection = { provider: string; name: string; baseUrl: string; contextWindow: string };

export function ModelPicker({ ready, busy, value, onChange, connectionOnly = false }: {
  ready: boolean; busy: boolean; value: ModelSelection; onChange: (value: ModelSelection) => void; connectionOnly?: boolean;
}) {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [dependenciesReady, setDependenciesReady] = useState(false);
  const [key, setKey] = useState("");
  const [clientId, setClientId] = useState("");
  const [message, setMessage] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [login, setLogin] = useState<Login | null>(null);
  const selected = providers.find(p => p.id === value.provider);

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    request<Provider[]>("/model-providers").then(items => { if (!cancelled) setProviders(items); })
      .catch(e => { if (!cancelled) setMessage(errorText(e)); });
    return () => { cancelled = true; };
  }, [ready]);

  useEffect(() => {
    setDependenciesReady(false); setKey(""); setMessage(""); setLogin(null);
    if (!ready || !value.provider) return;
    let cancelled = false;
    request(`/model-provider-dependencies?provider=${encodeURIComponent(value.provider)}`)
      .then(() => { if (!cancelled) setDependenciesReady(true); })
      .catch(e => { if (!cancelled) setMessage(errorText(e)); });
    return () => { cancelled = true; };
  }, [ready, value.provider]);

  useEffect(() => {
    if (!login || login.state !== "waiting") return;
    let cancelled = false;
    const timer = window.setInterval(() => {
      request<Login>(`/model-logins/${login.id}`).then(next => {
        if (cancelled) return;
        setLogin(next);
        if (next.state === "completed") setMessage("登录完成；凭据仅保留在本次服务进程。");
        if (next.state === "failed") setMessage(next.error || "登录失败。");
      }).catch(e => { if (!cancelled) setMessage(errorText(e)); });
    }, 1500);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [login]);

  async function connect(local = false) {
    setConnecting(true);
    try {
      await request("/model-connections", { provider: value.provider, api_key: local ? "" : key, model_base_url: value.baseUrl.trim() || null });
      setKey(""); setMessage("凭据已设置，仅保留在本次服务进程；尚未验证远端模型。");
    } catch (e) { setMessage(errorText(e)); }
    finally { setConnecting(false); }
  }

  async function beginLogin() {
    setConnecting(true);
    try {
      const next = await request<Login>("/model-logins", {
        provider: value.provider, ...(clientId ? { client_id: clientId } : {}),
      });
      setLogin(next); setMessage("请打开登录链接完成授权。");
    } catch (e) { setMessage(errorText(e)); }
    finally { setConnecting(false); }
  }

  return <details className="knowledge-picker">
    <summary>{connectionOnly ? "模型凭据" : "本次模型（可选）"}</summary>
    <p className="muted small">{connectionOnly ? "为本机服务设置凭据；当前会话继续使用已保存的模型。" : "留空使用默认偏好；本次模型选择只保存在这个会话。"}</p>
    {connectionOnly && value.baseUrl && <p className="muted small">API 地址：{value.baseUrl}</p>}
    <div className="row">
      <label>模型提供商
        <select value={value.provider} disabled={busy || connecting} onChange={e => onChange({ provider: e.target.value, name: "", baseUrl: "", contextWindow: "" })}>
          <option value="">使用默认偏好</option>
          {providers.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
        </select>
      </label>
      {!connectionOnly && <label>模型名称
        <input value={value.name} disabled={busy} list="provider-model-candidates" onChange={e => onChange({ ...value, name: e.target.value })} placeholder="选择或输入模型 ID" />
        <datalist id="provider-model-candidates">{selected?.models.map(name => <option key={name} value={name} />)}</datalist>
      </label>}
    </div>
    {selected && dependenciesReady && <>
      {selected.api_key_environment && <div className="row">
        <label>API Key<input type="password" value={key} autoComplete="off" disabled={busy || connecting} onChange={e => setKey(e.target.value)} placeholder={`或在启动服务前设置 ${selected.api_key_environment}`} /></label>
        <button type="button" disabled={busy || connecting || !key.trim()} onClick={() => void connect()}>设置 API Key</button>
      </div>}
      {selected.local_auth && <><p className="muted small">可使用本机已有凭据或登录状态；本地模型服务可按其配置无需密钥。</p>{value.baseUrl && <button type="button" disabled={busy || connecting} onClick={() => void connect(true)}>为此地址使用本机凭据</button>}</>}
      {selected.login && !value.baseUrl && <div className="row">
        {selected.login === "github-copilot" && <label>GitHub OAuth Client ID<input value={clientId} onChange={e => setClientId(e.target.value)} /></label>}
        <button type="button" disabled={busy || connecting || login?.state === "waiting"} onClick={() => void beginLogin()}>登录账号</button>
      </div>}
      {login?.state === "waiting" && <p><a href={login.url} target="_blank" rel="noreferrer">打开登录页面</a>{login.user_code && <> · 授权码：<code>{login.user_code}</code></>}
        <button type="button" onClick={() => { void request(`/model-logins/${login.id}`, {}, "DELETE").then(() => setLogin(null)).catch(e => setMessage(errorText(e))); }}>取消登录</button>
      </p>}
      {!connectionOnly && <details><summary>自定义接口与窗口</summary>
        {!value.provider.startsWith("opencode-") && value.provider !== "openai-codex" && <label>API 地址<input value={value.baseUrl} disabled={busy} onChange={e => onChange({ ...value, baseUrl: e.target.value })} placeholder="留空使用提供商默认地址" /></label>}
        <label>上下文窗口<input type="number" min={1024} value={value.contextWindow} disabled={busy} onChange={e => onChange({ ...value, contextWindow: e.target.value })} placeholder="仅在无法自动识别或自定义部署时填写" /></label>
      </details>}
    </>}
    {message && <p role="status" className="muted small">{message}</p>}
  </details>;
}
