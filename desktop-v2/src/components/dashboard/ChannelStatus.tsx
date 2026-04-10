import type { ChannelInfo } from "@/types/api";

export function ChannelStatus({ channels }: { channels: ChannelInfo[] }) {
  return (
    <div className="space-y-2">
      {channels.map((ch) => (
        <div key={ch.channel} className="flex items-center gap-2 text-sm">
          <span
            className={`w-2 h-2 rounded-full ${ch.connected ? "bg-green-400" : "bg-gray-500"}`}
          />
          <span className="text-text-primary">{ch.channel}</span>
          <span className="text-text-muted text-xs">
            {ch.connected ? "已连接" : "已断开"}
          </span>
        </div>
      ))}
    </div>
  );
}
