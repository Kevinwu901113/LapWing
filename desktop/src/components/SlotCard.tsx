type SlotCardProps = {
  slotId: string;
  slotName: string;
  description?: string;
  selectedProvider: string;
  selectedModel: string;
  providers: { id: string; name: string; models: string[] }[];
  onChange: (providerId: string, model: string) => void;
};

const selectStyle: React.CSSProperties = {
  background: "var(--bg-card)",
  border: "1px solid var(--border)",
  borderRadius: 4,
  padding: "5px 8px",
  fontSize: 13,
  color: "var(--text-primary)",
  cursor: "pointer",
  flex: 1,
};

export default function SlotCard({
  slotId,
  slotName,
  description,
  selectedProvider,
  selectedModel,
  providers,
  onChange,
}: SlotCardProps) {
  const activeProvider = providers.find((p) => p.id === selectedProvider);
  const modelOptions = activeProvider?.models ?? [];

  function handleProviderChange(e: React.ChangeEvent<HTMLSelectElement>) {
    const newProvider = providers.find((p) => p.id === e.target.value);
    const firstModel = newProvider?.models[0] ?? "";
    onChange(e.target.value, firstModel);
  }

  function handleModelChange(e: React.ChangeEvent<HTMLSelectElement>) {
    onChange(selectedProvider, e.target.value);
  }

  return (
    <div className="card" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div>
        <p style={{ margin: 0, fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
          {slotName}
        </p>
        {description && (
          <p style={{ margin: "2px 0 0", fontSize: 12, color: "var(--text-muted)" }}>
            {description}
          </p>
        )}
        <p style={{ margin: "2px 0 0", fontSize: 11, color: "var(--text-muted)" }}>{slotId}</p>
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <select style={selectStyle} value={selectedProvider} onChange={handleProviderChange}>
          <option value="">— 选择提供商 —</option>
          {providers.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        <select style={selectStyle} value={selectedModel} onChange={handleModelChange} disabled={modelOptions.length === 0}>
          <option value="">— 选择模型 —</option>
          {modelOptions.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}
