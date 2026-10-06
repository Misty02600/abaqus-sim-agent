import { useEffect, useState } from "react";
import { errorText, request } from "./api/client";
import type { components } from "./api/schema";

type Model = components["schemas"]["AvailableModel"];
type OutputSettings = components["schemas"]["ModelOutputSettings"];

export function ModelOutputSettings({ provider, disabled }: { provider: string; disabled: boolean }) {
  const [models, setModels] = useState<Model[]>([]);
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [loaded, setLoaded] = useState(false);
  const path = `/model-output-settings?provider=${encodeURIComponent(provider)}&model_name=${encodeURIComponent(name)}`;

  useEffect(() => {
    let cancelled = false;
    request<Model[]>("/models").then(items => {
      if (!cancelled) setModels(items.filter(item => item.provider === provider));
    }).catch(error => { if (!cancelled) setMessage(errorText(error)); });
    return () => { cancelled = true; };
  }, [provider]);

  useEffect(() => {
    let cancelled = false;
    setLoaded(false); setValue(""); setMessage("");
    if (!name) return;
    setLoading(true);
    request<OutputSettings>(path).then(settings => {
      if (!cancelled) { setValue(settings.max_tokens?.toString() ?? ""); setLoaded(true); }
    }).catch(error => { if (!cancelled) setMessage(errorText(error)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [name, path]);

  async function save() {
    const maxTokens = value.trim() ? Number(value) : null;
    if (maxTokens !== null && (!Number.isSafeInteger(maxTokens) || maxTokens <= 0)) {
      setMessage("请输入正整数，或留空恢复默认值。"); return;
    }
    setSaving(true);
    try {
      await request<OutputSettings>(path, { max_tokens: maxTokens }, "PUT");
      setMessage("已保存，CLI 和网页的新会话共用；已有会话保持原值。");
    } catch (error) { setMessage(errorText(error)); }
    finally { setSaving(false); }
  }

  return <details>
    <summary>模型高级设置</summary>
    <label>模型
      <select value={name} disabled={disabled || saving} onChange={event => { setLoaded(false); setName(event.target.value); }}>
        <option value="">选择需要设置的模型</option>
        {models.map(model => <option key={model.name} value={model.name}>{model.name}</option>)}
      </select>
    </label>
    {name && <>
      <label>最大输出 token
        <input type="number" min="1" step="1" value={value} disabled={disabled || loading || saving || !loaded} onChange={event => setValue(event.target.value)} placeholder="服务商默认值" />
      </label>
      <p className="muted small">留空沿用服务商默认值。设置值须在该服务支持的范围内，不改变推理强度。</p>
      <button type="button" disabled={disabled || loading || saving || !loaded} onClick={() => void save()}>保存输出设置</button>
    </>}
    {message && <p role="status" className="muted small">{message}</p>}
  </details>;
}
