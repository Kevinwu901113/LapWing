with open("src/core/task_runtime.py", "r") as f:
    code = f.read()

# Remove _record_tool_denied
start_idx = code.find("    async def _record_tool_denied(")
end_idx = code.find("    async def execute_tool(")
code = code[:start_idx] + code[end_idx:]

# Remove _blocked_payload
start_idx = code.find("    def _blocked_payload(")
end_idx = code.find("    def tool_fallback_reply(")
code = code[:start_idx] + code[end_idx:]

with open("src/core/task_runtime.py", "w") as f:
    f.write(code)
