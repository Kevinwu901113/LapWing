import { ScrollArea } from "@/components/ui/scroll-area";

export default function SensingPage() {
  // Sensing data comes from Tauri commands (Rust-side), which are stubs on Linux.
  // Will populate when building the Rust sensing modules.
  return (
    <div className="h-full flex flex-col">
      <div className="p-4 border-b border-surface-border">
        <h1 className="text-lg font-medium text-text-accent">环境感知</h1>
      </div>
      <ScrollArea className="flex-1 px-4 py-4">
        <div className="space-y-4">
          <div className="bg-surface border border-surface-border rounded-lg p-4">
            <h2 className="text-sm font-medium text-text-accent mb-2">当前状态</h2>
            <div className="flex items-center gap-2 text-sm text-text-secondary">
              <span className="w-2 h-2 rounded-full bg-green-400" />
              正常模式
            </div>
          </div>

          <div className="bg-surface border border-surface-border rounded-lg p-4">
            <h2 className="text-sm font-medium text-text-accent mb-2">今日应用使用</h2>
            <div className="text-sm text-text-muted">
              感知模块需要 Windows 环境运行。Linux 开发模式下数据为空。
            </div>
          </div>

          <div className="bg-surface border border-surface-border rounded-lg p-4">
            <h2 className="text-sm font-medium text-text-accent mb-2">会话事件</h2>
            <div className="text-sm text-text-muted">
              待 Rust 感知模块实现后自动显示。
            </div>
          </div>
        </div>
      </ScrollArea>
    </div>
  );
}
