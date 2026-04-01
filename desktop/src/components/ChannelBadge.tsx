type ChannelBadgeProps = {
  channel: "telegram" | "qq" | "desktop";
  enabled: boolean;
  status?: string;
};

const CHANNEL_NAMES: Record<ChannelBadgeProps["channel"], string> = {
  telegram: "电报",
  qq: "QQ",
  desktop: "桌面端",
};

const CHANNEL_COLORS: Record<ChannelBadgeProps["channel"], string> = {
  telegram: "var(--blue)",
  qq: "var(--accent)",
  desktop: "var(--green)",
};

export default function ChannelBadge({ channel, enabled, status }: ChannelBadgeProps) {
  const color = CHANNEL_COLORS[channel];

  return (
    <div style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <span
        style={{
          width: 8,
          height: 8,
          borderRadius: "50%",
          background: enabled ? color : "var(--text-muted)",
          flexShrink: 0,
        }}
      />
      <span style={{ fontSize: 13, color: "var(--text-primary)" }}>
        {CHANNEL_NAMES[channel]}
      </span>
      {status && (
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>{status}</span>
      )}
      {!status && (
        <span style={{ fontSize: 12, color: enabled ? "var(--green)" : "var(--text-muted)" }}>
          {enabled ? "已启用" : "已禁用"}
        </span>
      )}
    </div>
  );
}
