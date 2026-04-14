---
source: incident
incident_id: INC-20260413-0004
created: 2026-04-13
tool: schedule_task
---

遇到 schedule_task 返回 db_error 时，先检查触发时间是否已过去，若是则使用未来时间重新安排任务。
