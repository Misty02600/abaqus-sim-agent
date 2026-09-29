import { useCallback, useEffect, useId, useMemo, useRef, useState, type CSSProperties } from "react";
import { request, errorText } from "./api/client";

type Provider = { id: string; label: string; api_key_environment: string | null; local_auth: boolean; login: string | null };
type AvailableModel = { provider: string; name: string; context_window: number };
export type ModelSelection = { provider: string; name: string };

export function ModelPicker({ ready, busy, value, onChange, onValidityChange, refreshKey = 0, onOpenSettings }: {
  ready: boolean; busy: boolean; value: ModelSelection; onChange: (value: ModelSelection) => void; onValidityChange?: (valid: boolean) => void; refreshKey?: number; onOpenSettings: () => void;
}) {
  const panelId = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const search = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const [position, setPosition] = useState<CSSProperties>({});
  const [providers, setProviders] = useState<Provider[]>([]);
  const [models, setModels] = useState<AvailableModel[]>([]);
  const [configured, setConfigured] = useState<string[]>([]);
  const [message, setMessage] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const selectionAvailable = !!value.provider && !!value.name && configured.includes(value.provider) && models.some(m => m.provider === value.provider && m.name === value.name);
  const providerLabel = (id: string) => providers.find(p => p.id === id)?.label ?? id;
  const filteredModels = useMemo(() => {
    const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return models.filter(m => {
      const label = providers.find(p => p.id === m.provider)?.label ?? m.provider;
      const text = `${m.name} ${m.provider} ${label}`.toLowerCase();
      return terms.every(term => text.includes(term));
    });
  }, [models, providers, query]);
  const emptyProviders = providers.filter(p => configured.includes(p.id)
    && !models.some(m => m.provider === p.id)
    && `${p.id} ${p.label}`.toLowerCase().includes(query.trim().toLowerCase()));
  useEffect(() => {
    if (panel.current?.matches(":popover-open")) {
      document.getElementById(`${panelId}-model-${activeIndex}`)?.scrollIntoView({ block: "nearest" });
    }
  }, [activeIndex, filteredModels, panelId]);
  function chooseModel(model: AvailableModel) {
    onChange({ provider: model.provider, name: model.name });
    closePanel();
  }
  function positionPanel() {
    const rect = trigger.current?.getBoundingClientRect();
    if (!rect) return;
    const above = rect.top - 12;
    const below = window.innerHeight - rect.bottom - 12;
    const openAbove = above > below;
    const width = Math.min(360, window.innerWidth - 24);
    setPosition({
      left: Math.max(12, Math.min(rect.left, window.innerWidth - width - 12)),
      ...(openAbove ? { bottom: window.innerHeight - rect.top + 8 } : { top: rect.bottom + 8 }),
      maxHeight: Math.max(120, (openAbove ? above : below) - 8),
    });
  }
  function closePanel() {
    panel.current?.hidePopover();
    trigger.current?.focus();
  }
  useEffect(() => {
    onValidityChange?.(selectionAvailable);
  }, [selectionAvailable, onValidityChange]);
  const refreshModels = useCallback(async (fetchLatest = false) => {
    const connected = await request<string[]>("/model-connections");
    setConfigured(connected);
    const available = await request<AvailableModel[]>(fetchLatest ? "/models?refresh=true" : "/models");
    setModels(available);
    return available;
  }, []);

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    Promise.all([request<Provider[]>("/model-providers"), request<string[]>("/model-connections")])
      .then(([items, connected]) => { if (!cancelled) { setProviders(items); setConfigured(connected); } })
      .catch(e => { if (!cancelled) setMessage(errorText(e)); });
    request<AvailableModel[]>("/models")
      .then(available => { if (!cancelled) setModels(available); })
      .catch(e => { if (!cancelled) setMessage(errorText(e)); });
    return () => { cancelled = true; };
  }, [ready, refreshKey]);

  async function refreshCatalog() {
    setRefreshing(true);
    try {
      await refreshModels(true);
      setMessage("模型列表已重新载入。");
    } catch (e) { setMessage(errorText(e)); }
    finally { setRefreshing(false); }
  }

  return <div className="model-picker">
    <button type="button" ref={trigger} className="model-picker-trigger"
      disabled={!ready || busy} popoverTarget={panelId} aria-haspopup="dialog"
      title={value.name ? `${value.provider}/${value.name}` : "选择本次对话使用的模型"}
      onClick={positionPanel}>
      <span>{value.name || "选择模型"}</span>
      <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="m4 6 4 4 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
    </button>
    <div ref={panel} id={panelId} popover="auto" role="dialog"
      aria-label="选择模型" className="model-picker-panel" style={position}
      onToggle={event => {
        if (event.newState === "open") {
          setQuery(""); setActiveIndex(0);
          search.current?.focus();
        }
      }}>
    <div className="row model-picker-heading">
      <strong>选择模型</strong>
      <button type="button" className="subtle" aria-label="关闭模型选择" onClick={closePanel}>×</button>
    </div>
      <input ref={search} type="search" role="combobox" aria-label="搜索已有模型"
        aria-autocomplete="list" aria-expanded="true" aria-controls={`${panelId}-models`}
        aria-activedescendant={filteredModels[activeIndex] ? `${panelId}-model-${activeIndex}` : undefined}
        placeholder="搜索模型或提供商…" value={query} disabled={!ready || busy}
        onChange={event => { setQuery(event.target.value); setActiveIndex(0); }}
        onKeyDown={event => {
          if (event.nativeEvent.isComposing) return;
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            setActiveIndex(i => Math.max(0, Math.min(filteredModels.length - 1, i + (event.key === "ArrowDown" ? 1 : -1))));
          } else if (event.key === "Enter") {
            event.preventDefault();
            if (filteredModels[activeIndex]) chooseModel(filteredModels[activeIndex]);
          }
        }} />
      <div id={`${panelId}-models`} role="listbox" aria-label="可选模型" className="model-picker-list">
        {filteredModels.map((model, index) => <button type="button" role="option"
          id={`${panelId}-model-${index}`} key={`${model.provider}/${model.name}`}
          className={`model-choice ${index === activeIndex ? "is-highlighted" : ""}`}
          aria-selected={model.provider === value.provider && model.name === value.name}
          disabled={busy} title={`${model.name} · ${providerLabel(model.provider)}`}
          onPointerMove={() => setActiveIndex(index)} onClick={() => chooseModel(model)}>
          <span className="model-choice-label"><span className="model-choice-name">{model.name}</span><small>{providerLabel(model.provider)}</small></span>
          <span className="model-choice-check" aria-hidden="true">{model.provider === value.provider && model.name === value.name ? "✓" : ""}</span>
        </button>)}
      </div>
      {!filteredModels.length && !emptyProviders.length && <p className="muted small">{configured.length ? "没有匹配的可选模型。" : "请在设置中登录或填写 API Key。"}</p>}
      {emptyProviders.map(p => <p className="model-picker-empty-provider muted small" key={p.id}>{p.label} · 暂无可选模型</p>)}
      {value.name && !selectionAvailable && <p className="muted small">原选择已不可用，请重新选择。</p>}
      <button type="button" className="subtle" disabled={busy || refreshing} onClick={() => void refreshCatalog()}>{refreshing ? "正在刷新…" : "刷新模型"}</button>
    <button type="button" className="subtle" onClick={() => { closePanel(); onOpenSettings(); }}>登录 / 设置 API Key</button>
    {message && <p role="status" className="muted small">{message}</p>}
    </div>
  </div>;
}
