import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ConnectionTab } from "@/components/settings/ConnectionTab";
import { ModelsTab } from "@/components/settings/ModelsTab";
import { PermissionsTab } from "@/components/settings/PermissionsTab";

export default function SettingsPage() {
  return (
    <div className="h-full flex flex-col">
      <div className="p-4 border-b border-surface-border">
        <h1 className="text-lg font-medium text-text-accent">Settings</h1>
      </div>

      <Tabs defaultValue="connection" className="flex-1 flex flex-col">
        <TabsList className="mx-4 mt-2 bg-void-50">
          <TabsTrigger value="connection">Connection</TabsTrigger>
          <TabsTrigger value="models">Models</TabsTrigger>
          <TabsTrigger value="permissions">Permissions</TabsTrigger>
          <TabsTrigger value="about">About</TabsTrigger>
        </TabsList>

        <TabsContent value="connection" className="flex-1 m-0">
          <ScrollArea className="h-full px-4 py-4">
            <ConnectionTab />
          </ScrollArea>
        </TabsContent>

        <TabsContent value="models" className="flex-1 m-0">
          <ScrollArea className="h-full px-4 py-4">
            <ModelsTab />
          </ScrollArea>
        </TabsContent>

        <TabsContent value="permissions" className="flex-1 m-0">
          <ScrollArea className="h-full px-4 py-4">
            <PermissionsTab />
          </ScrollArea>
        </TabsContent>

        <TabsContent value="about" className="flex-1 m-0">
          <ScrollArea className="h-full px-4 py-4">
            <div className="space-y-3 text-sm">
              <div className="text-text-accent">Lapwing Desktop v0.2.0</div>
              <div className="text-text-secondary">
                Built with Tauri v2 + React 19 + TypeScript
              </div>
              <div className="text-text-muted text-xs">
                Phase 7 — "Her Room"
              </div>
            </div>
          </ScrollArea>
        </TabsContent>
      </Tabs>
    </div>
  );
}
