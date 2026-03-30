import DataCard from "../components/DataCard";

export default function SettingsPage() {
  return (
    <div className="page">
      <header className="page-header animate-in">
        <div>
          <h1 className="page-title">设置</h1>
          <p className="page-subtitle">系统配置（即将推出）</p>
        </div>
      </header>

      <DataCard title="配置管理" className="stagger-1">
        <p className="empty-state">
          此页面将支持在线编辑 .env 配置、管理 Heartbeat 参数、调整 LLM 路由策略等。
          目前请直接编辑服务器上的配置文件。
        </p>
      </DataCard>
    </div>
  );
}
