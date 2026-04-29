import asyncio
from src.core.runtime_profiles import LOCAL_EXECUTION_PROFILE
from tests.core.test_runtime_profiles_exclusion import _make_full_registry, _resolve_tool_names

registry = _make_full_registry()
names = _resolve_tool_names(registry, LOCAL_EXECUTION_PROFILE)
print(sorted(list(names)))
