import re

with open("tmp_dispatch.py", "r") as f:
    code = f.read()

code = code.replace("async def execute_tool(", "async def dispatch(")

code = re.sub(r'self\._resolve_profile', r'self._runtime._resolve_profile', code)
code = re.sub(r'self\._tool_names_for_profile', r'self._runtime._tool_names_for_profile', code)
code = re.sub(r'getattr\(self,\s*"_checkpoint_manager"', r'getattr(self._runtime, "_checkpoint_manager"', code)
code = re.sub(r'getattr\(self,\s*"_browser_guard"', r'getattr(self._runtime, "_browser_guard"', code)
code = re.sub(r'self\._pending_shell_confirmations', r'self._runtime._pending_shell_confirmations', code)
code = re.sub(r'self\._record_simulated_tool_call', r'self._runtime._record_simulated_tool_call', code)
code = re.sub(r'self\._tool_budget_tracker', r'self._runtime._tool_budget_tracker', code)
code = re.sub(r'self\._memory_index', r'self._runtime._memory_index', code)
code = re.sub(r'self\._try_ambient_cache', r'self._runtime._try_ambient_cache', code)
code = re.sub(r'self\._writeback_to_ambient', r'self._runtime._writeback_to_ambient', code)
code = re.sub(r'self\._shell_failure_reason', r'self._runtime._shell_failure_reason', code)
code = re.sub(r'self\._publish_task_event', r'self._runtime._publish_task_event', code)

with open("tmp_dispatch.py", "w") as f:
    f.write(code)
