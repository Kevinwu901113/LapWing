"""TrustTagger — 为进入上下文的内容添加信任标记。Phase 1 新基础设施。"""

from __future__ import annotations


class TrustTagger:
    """为进入上下文的内容添加信任标记。"""

    @staticmethod
    def tag_kevin(content: str, source: str, timestamp: str) -> str:
        return (
            f'<kevin_message source="{source}" timestamp="{timestamp}">\n'
            f'{content}\n'
            f'</kevin_message>'
        )

    @staticmethod
    def tag_group(
        content: str, sender_id: str, sender_name: str, trust: str
    ) -> str:
        return (
            f'<group_message source="qq_group" sender_id="{sender_id}" '
            f'sender_name="{sender_name}" trust="{trust}">\n'
            f'{content}\n'
            f'</group_message>'
        )

    @staticmethod
    def tag_external(content: str, source_url: str) -> str:
        return (
            f'<external_content source="web" url="{source_url}" trust="untrusted">\n'
            f'{content}\n'
            f'</external_content>'
        )

    @staticmethod
    def tag_agent(content: str, agent: str, task_id: str) -> str:
        return (
            f'<agent_result agent="{agent}" task_id="{task_id}" trust="agent">\n'
            f'{content}\n'
            f'</agent_result>'
        )
