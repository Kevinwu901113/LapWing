import re

with open("src/core/task_runtime.py", "r") as f:
    code = f.read()

start_marker = "    async def execute_tool("
end_marker = "    async def _execute_tool_call("

start_idx = code.find(start_marker)
end_idx = code.find(end_marker)

new_method = """    async def execute_tool(
        self,
        *,
        request: ToolExecutionRequest,
        profile: str | RuntimeProfile,
        state: ExecutionSessionState | None = None,
        deps: RuntimeDeps | None = None,
        task_id: str | None = None,
        chat_id: str | None = None,
        event_bus=None,
        workspace_root: str | None = None,
        services: dict[str, Any] | None = None,
        adapter: str = "",
        user_id: str = "",
        send_fn: Callable[[str], "Awaitable[Any]"] | None = None,
        focus_id: str | None = None,
    ) -> ToolExecutionResult:
        return await self.tool_dispatcher.dispatch(
            request=request,
            profile=profile,
            state=state,
            deps=deps,
            task_id=task_id,
            chat_id=chat_id,
            event_bus=event_bus,
            workspace_root=workspace_root,
            services=services,
            adapter=adapter,
            user_id=user_id,
            send_fn=send_fn,
            focus_id=focus_id,
        )

"""

new_code = code[:start_idx] + new_method + code[end_idx:]

with open("src/core/task_runtime.py", "w") as f:
    f.write(new_code)
