"""BrowserManager — Playwright 浏览器子系统。

使用 Playwright 持久化上下文控制 Chromium，提供 DOM 提取、Tab 管理、
结构化页面状态输出供 LLM 消费。Phase 1：纯 DOM 方式，无视觉理解。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.settings import (
    BROWSER_ACTION_TIMEOUT_MS,
    BROWSER_HEADLESS,
    BROWSER_LOCALE,
    BROWSER_MAX_ELEMENT_COUNT,
    BROWSER_MAX_TABS,
    BROWSER_NAVIGATION_TIMEOUT_MS,
    BROWSER_PAGE_TEXT_MAX_CHARS,
    BROWSER_SCREENSHOT_DIR,
    BROWSER_SCREENSHOT_RETAIN_DAYS,
    BROWSER_TIMEZONE,
    BROWSER_USER_DATA_DIR,
    BROWSER_VIEWPORT_HEIGHT,
    BROWSER_VIEWPORT_WIDTH,
    BROWSER_WAIT_AFTER_ACTION_MS,
    BROWSER_VISION_ALT_RATIO_THRESHOLD,
    BROWSER_VISION_CACHE_TTL_SECONDS,
    BROWSER_VISION_ENABLED,
    BROWSER_VISION_IMG_THRESHOLD,
    BROWSER_VISION_MAX_DESCRIPTION_CHARS,
    BROWSER_VISION_SLOT,
)

logger = logging.getLogger("lapwing.core.browser_manager")


# ── 异常层级 ─────────────────────────────────────────────────────────────────


class BrowserError(Exception):
    pass


class BrowserNotStartedError(BrowserError):
    pass


class BrowserNavigationError(BrowserError):
    pass


class BrowserElementNotFoundError(BrowserError):
    pass


class BrowserTabNotFoundError(BrowserError):
    pass


class BrowserTimeoutError(BrowserError):
    pass


# ── 数据模型 ─────────────────────────────────────────────────────────────────

_TAG_LABEL_MAP: dict[str, str] = {
    "button": "按钮",
    "a": "链接",
    "input": "输入框",
    "select": "下拉框",
    "textarea": "文本域",
}


@dataclass
class InteractiveElement:
    index: int
    tag: str
    element_type: str | None
    text: str
    name: str | None
    aria_label: str | None
    href: str | None
    value: str | None
    is_visible: bool
    selector: str

    def to_label(self) -> str:
        """生成 LLM 可读的元素描述行。"""
        label = _TAG_LABEL_MAP.get(self.tag, self.tag)
        parts: list[str] = [f"[{self.index}]", label]

        if self.tag == "input" and self.element_type and self.element_type != "text":
            parts.append(f"({self.element_type})")

        desc = self.text or self.aria_label or self.name or ""
        if desc:
            parts.append(f'"{desc}"')

        if self.tag == "a" and self.href:
            parts.append(f"→ {self.href}")

        if self.value:
            parts.append(f"[值={self.value}]")

        return " ".join(parts)


@dataclass
class TabInfo:
    tab_id: str
    url: str
    title: str
    is_active: bool
    last_accessed: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class PageState:
    url: str
    title: str
    elements: list[InteractiveElement]
    text_summary: str
    visual_description: str | None
    scroll_position: str
    has_more_below: bool
    tab_id: str
    timestamp: str
    is_image_heavy: bool

    def to_llm_text(self, max_elements: int = 40) -> str:
        """格式化为 LLM 可读的文本。"""
        position_map = {"top": "顶部", "middle": "中部", "bottom": "底部"}
        pos_label = position_map.get(self.scroll_position, self.scroll_position)
        more = "（下方有更多内容）" if self.has_more_below else ""

        lines: list[str] = [
            f"[页面] {self.title}",
            f"URL: {self.url} | 位置: {pos_label}{more}",
            "",
        ]

        visible = [e for e in self.elements[:max_elements] if e.is_visible]
        if visible:
            lines.append("可交互元素：")
            for elem in visible:
                lines.append(elem.to_label())
            lines.append("")

        if self.visual_description:
            lines.append("页面视觉内容：")
            lines.append(self.visual_description)
            lines.append("")

        if self.text_summary:
            lines.append("页面文字内容：" if self.visual_description else "页面内容：")
            lines.append(self.text_summary)

        return "\n".join(lines)


# ── DOM 处理器 ────────────────────────────────────────────────────────────────

# 注入浏览器的 JS：遍历 DOM 提取可交互元素和页面指标
_EXTRACT_ELEMENTS_JS = """
() => {
    const selectors = 'a, button, input, select, textarea, [role="button"], [onclick]';
    const nodes = document.querySelectorAll(selectors);
    const elements = [];
    let idx = 1;
    const tagCounts = {};

    // 页面指标
    let imgCount = 0;
    let imgWithAltCount = 0;
    let textNodeCharCount = 0;
    let canvasCount = 0;

    // 统计图片
    document.querySelectorAll('img').forEach(img => {
        imgCount++;
        if (img.alt && img.alt.trim()) imgWithAltCount++;
    });

    // 统计 canvas
    canvasCount = document.querySelectorAll('canvas').length;

    // 统计文本节点字符数
    const walker = document.createTreeWalker(
        document.body || document.documentElement,
        NodeFilter.SHOW_TEXT,
        null,
        false
    );
    while (walker.nextNode()) {
        textNodeCharCount += (walker.currentNode.textContent || '').trim().length;
    }

    nodes.forEach(node => {
        const style = window.getComputedStyle(node);
        if (style.display === 'none' || style.visibility === 'hidden') return;
        const rect = node.getBoundingClientRect();
        if (rect.width === 0 && rect.height === 0) return;

        const tag = node.tagName.toLowerCase();
        const elType = node.type || null;
        const ariaLabel = node.getAttribute('aria-label') || null;
        const innerText = (node.innerText || '').trim().substring(0, 100);
        const placeholder = node.placeholder || null;
        const name = node.name || null;
        const title = node.title || null;
        const href = tag === 'a' ? node.getAttribute('href') : null;
        const value = (tag === 'input' || tag === 'select' || tag === 'textarea')
            ? (node.value || null) : null;

        // 描述文本优先级: aria-label > innerText > placeholder > name > title
        const text = ariaLabel || innerText || placeholder || name || title || '';

        // 生成唯一选择器
        tagCounts[tag] = (tagCounts[tag] || 0) + 1;
        let selector;
        const id = node.id;
        if (id) {
            selector = '#' + CSS.escape(id);
        } else {
            // 使用 nth-of-type
            const parent = node.parentElement;
            if (parent) {
                const siblings = parent.querySelectorAll(':scope > ' + tag);
                let nth = 1;
                for (let i = 0; i < siblings.length; i++) {
                    if (siblings[i] === node) { nth = i + 1; break; }
                }
                // 构建到父元素的路径
                let parentSel = '';
                if (parent.id) {
                    parentSel = '#' + CSS.escape(parent.id);
                } else if (parent.tagName.toLowerCase() !== 'html') {
                    parentSel = parent.tagName.toLowerCase();
                    if (parent.className && typeof parent.className === 'string') {
                        const cls = parent.className.trim().split(/\\s+/)[0];
                        if (cls) parentSel += '.' + CSS.escape(cls);
                    }
                }
                selector = (parentSel ? parentSel + ' > ' : '') + tag + ':nth-of-type(' + nth + ')';
            } else {
                selector = tag + ':nth-of-type(' + tagCounts[tag] + ')';
            }
        }

        const isVisible = rect.top < window.innerHeight && rect.bottom > 0
            && rect.left < window.innerWidth && rect.right > 0;

        elements.push({
            index: idx++,
            tag,
            element_type: elType,
            text,
            name,
            aria_label: ariaLabel,
            href,
            value,
            is_visible: isVisible,
            selector
        });
    });

    return {
        elements,
        metrics: {
            img_count: imgCount,
            img_with_alt_count: imgWithAltCount,
            text_node_char_count: textNodeCharCount,
            canvas_count: canvasCount
        }
    };
}
"""

_EXTRACT_TEXT_JS = """
(args) => {
    const { selector, maxChars } = args;
    let root;
    if (selector) {
        root = document.querySelector(selector);
    }
    if (!root) {
        root = document.querySelector('main')
            || document.querySelector('article')
            || document.body;
    }
    if (!root) return '';
    let text = (root.innerText || '').trim();
    // 合并连续空行
    text = text.replace(/\\n{3,}/g, '\\n\\n');
    if (text.length > maxChars) {
        text = text.substring(0, maxChars) + '…';
    }
    return text;
}
"""

_SCROLL_INFO_JS = """
() => {
    const scrollY = window.scrollY || window.pageYOffset || 0;
    const scrollHeight = document.documentElement.scrollHeight || document.body.scrollHeight;
    const clientHeight = window.innerHeight;
    const atBottom = (scrollY + clientHeight) >= (scrollHeight - 50);
    let position;
    if (scrollY < 100) {
        position = 'top';
    } else if (atBottom) {
        position = 'bottom';
    } else {
        position = 'middle';
    }
    const hasMoreBelow = !atBottom && scrollHeight > clientHeight;
    return { position, hasMoreBelow };
}
"""


class DOMProcessor:
    """注入 JavaScript 遍历 DOM 并提取可交互元素。"""

    async def extract_elements(self, page: Any) -> tuple[list[InteractiveElement], dict]:
        """执行注入的 JS 提取所有可交互元素。

        返回 (elements_list, page_metrics)。
        page_metrics = {
            "img_count": int,
            "img_with_alt_count": int,
            "text_node_char_count": int,
            "canvas_count": int,
        }
        """
        try:
            result = await page.evaluate(_EXTRACT_ELEMENTS_JS)
        except Exception as exc:
            logger.warning("DOM 元素提取失败: %s", exc)
            return [], {
                "img_count": 0,
                "img_with_alt_count": 0,
                "text_node_char_count": 0,
                "canvas_count": 0,
            }

        raw_elements = result.get("elements", [])
        metrics = result.get("metrics", {})

        elements: list[InteractiveElement] = []
        max_count = BROWSER_MAX_ELEMENT_COUNT
        for item in raw_elements[:max_count]:
            elements.append(InteractiveElement(
                index=item["index"],
                tag=item["tag"],
                element_type=item.get("element_type"),
                text=item.get("text", ""),
                name=item.get("name"),
                aria_label=item.get("aria_label"),
                href=item.get("href"),
                value=item.get("value"),
                is_visible=item.get("is_visible", True),
                selector=item.get("selector", ""),
            ))

        return elements, metrics

    async def extract_text(
        self,
        page: Any,
        selector: str | None = None,
        max_chars: int | None = None,
    ) -> str:
        """提取页面文本。

        如果指定 selector，从该元素提取。
        否则优先 <main>/<article>，回退到 <body>。
        合并连续空行，截断到 max_chars。
        """
        if max_chars is None:
            max_chars = BROWSER_PAGE_TEXT_MAX_CHARS
        try:
            text = await page.evaluate(
                _EXTRACT_TEXT_JS,
                {"selector": selector, "maxChars": max_chars},
            )
            return text or ""
        except Exception as exc:
            logger.warning("页面文本提取失败: %s", exc)
            return ""

    async def get_scroll_info(self, page: Any) -> tuple[str, bool]:
        """返回 (scroll_position, has_more_below)。

        scroll_position: "top" if scrollY < 100, "bottom" if at bottom, else "middle"
        has_more_below: True if there's more content below viewport
        """
        try:
            info = await page.evaluate(_SCROLL_INFO_JS)
            return info.get("position", "top"), info.get("hasMoreBelow", False)
        except Exception as exc:
            logger.warning("滚动信息获取失败: %s", exc)
            return "top", False


# ── 浏览器管理器 ──────────────────────────────────────────────────────────────


class BrowserManager:
    """Playwright 浏览器管理器，提供持久化上下文、Tab 管理、DOM 处理。"""

    def __init__(self) -> None:
        self._playwright: Any = None
        self._context: Any = None  # 持久化浏览器上下文
        self._tabs: dict[str, tuple[Any, TabInfo]] = {}  # tab_id -> (page, info)
        self._active_tab_id: str | None = None
        self._element_map: dict[str, dict[int, str]] = {}  # tab_id -> {index: selector}
        self._lock = asyncio.Lock()
        self._dom_processor = DOMProcessor()
        # 可选依赖（通过 container 注入）
        self._router: Any = None  # LLMRouter（视觉理解用）
        self._vlm_client: Any = None  # MiniMaxVLM（VLM 端点，优先于 router）
        self._event_bus: Any = None  # DesktopEventBus
        self._browser_guard: Any = None  # BrowserGuard（JS 安全检查）
        # 视觉描述缓存：{tab_id: (timestamp, description)}
        self._vision_cache: dict[str, tuple[float, str]] = {}

    def set_router(self, router: Any) -> None:
        self._router = router

    def set_vlm_client(self, client: Any) -> None:
        """设置 MiniMax VLM 客户端（优先用于视觉理解）。"""
        self._vlm_client = client

    def set_event_bus(self, event_bus: Any) -> None:
        self._event_bus = event_bus

    def set_browser_guard(self, guard: Any) -> None:
        self._browser_guard = guard

    # ── 属性 ──

    @property
    def is_started(self) -> bool:
        return self._context is not None

    # ── 生命周期 ──

    async def start(self) -> None:
        """启动 Playwright + 持久化上下文。创建数据目录。"""
        from playwright.async_api import async_playwright

        if self._context is not None:
            logger.warning("BrowserManager 已启动，跳过重复启动")
            return

        # 确保目录存在
        user_data_path = Path(BROWSER_USER_DATA_DIR)
        user_data_path.mkdir(parents=True, exist_ok=True)
        screenshot_path = Path(BROWSER_SCREENSHOT_DIR)
        screenshot_path.mkdir(parents=True, exist_ok=True)
        state_dir = user_data_path.parent
        state_dir.mkdir(parents=True, exist_ok=True)

        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(user_data_path),
            headless=BROWSER_HEADLESS,
            viewport={"width": BROWSER_VIEWPORT_WIDTH, "height": BROWSER_VIEWPORT_HEIGHT},
            locale=BROWSER_LOCALE,
            timezone_id=BROWSER_TIMEZONE,
            args=["--disable-blink-features=AutomationControlled"],
        )

        # 设置默认超时
        self._context.set_default_navigation_timeout(BROWSER_NAVIGATION_TIMEOUT_MS)
        self._context.set_default_timeout(BROWSER_ACTION_TIMEOUT_MS)

        logger.info(
            "BrowserManager 已启动 (headless=%s, viewport=%dx%d)",
            BROWSER_HEADLESS,
            BROWSER_VIEWPORT_WIDTH,
            BROWSER_VIEWPORT_HEIGHT,
        )

    async def stop(self) -> None:
        """保存 Tab 状态，优雅关闭所有资源。"""
        if self._context is None:
            return

        # 保存 Tab 状态
        await self._save_tab_state()

        try:
            await self._context.close()
        except Exception as exc:
            logger.warning("关闭浏览器上下文异常: %s", exc)

        try:
            await self._playwright.stop()
        except Exception as exc:
            logger.warning("停止 Playwright 异常: %s", exc)

        self._context = None
        self._playwright = None
        self._tabs.clear()
        self._active_tab_id = None
        self._element_map.clear()
        logger.info("BrowserManager 已停止")

    # ── 导航 ──

    async def navigate(self, url: str, tab_id: str | None = None) -> PageState:
        """导航到指定 URL。tab_id 为 None 时创建新 Tab。等待加载完成后返回 PageState。"""
        async with self._lock:
            self._ensure_started()

            if tab_id is None:
                tab_info = await self._create_tab_unlocked(url=None)
                tab_id = tab_info.tab_id

            page = self._resolve_tab(tab_id)

            await self._publish_event("browser.navigating", {"tab_id": tab_id, "url": url})
            try:
                await page.goto(url, wait_until="domcontentloaded")
            except Exception as exc:
                error_msg = str(exc)
                await self._publish_event("browser.error", {"tab_id": tab_id, "error": error_msg, "action": "navigate"})
                if "timeout" in error_msg.lower() or "Timeout" in error_msg:
                    raise BrowserTimeoutError(f"导航超时: {url}") from exc
                raise BrowserNavigationError(f"导航失败: {url} — {error_msg}") from exc

            await self._wait_for_stable(page)
            self._active_tab_id = tab_id
            self._update_tab_info(tab_id, page)
            title = await page.title()
            await self._publish_event("browser.navigated", {"tab_id": tab_id, "url": page.url, "title": title})
            return await self._build_page_state(page, tab_id)

    async def get_page_state(self, tab_id: str | None = None) -> PageState:
        """获取当前页面状态（不导航）。"""
        async with self._lock:
            self._ensure_started()
            tab_id = tab_id or self._active_tab_id
            if tab_id is None:
                raise BrowserTabNotFoundError("没有活跃的 Tab")
            page = self._resolve_tab(tab_id)
            return await self._build_page_state(page, tab_id)

    # ── 交互操作 ──

    async def click(self, element_ref: str, tab_id: str | None = None) -> PageState:
        """点击元素。

        element_ref: '[3]' 用索引, 'css:xxx' 用 CSS 选择器, 'text:xxx' 用文本。
        """
        async with self._lock:
            self._ensure_started()
            tab_id = tab_id or self._active_tab_id
            if tab_id is None:
                raise BrowserTabNotFoundError("没有活跃的 Tab")
            page = self._resolve_tab(tab_id)

            locator = await self._resolve_element(page, element_ref, tab_id)
            try:
                await locator.click(timeout=BROWSER_ACTION_TIMEOUT_MS)
            except Exception as exc:
                if "timeout" in str(exc).lower():
                    raise BrowserTimeoutError(f"点击超时: {element_ref}") from exc
                raise

            await self._wait_for_stable(page)
            self._update_tab_info(tab_id, page)
            return await self._build_page_state(page, tab_id)

    async def type_text(
        self,
        element_ref: str,
        text: str,
        *,
        clear_first: bool = True,
        press_enter: bool = False,
        tab_id: str | None = None,
    ) -> PageState:
        """在输入框中输入文本。"""
        async with self._lock:
            self._ensure_started()
            tab_id = tab_id or self._active_tab_id
            if tab_id is None:
                raise BrowserTabNotFoundError("没有活跃的 Tab")
            page = self._resolve_tab(tab_id)

            locator = await self._resolve_element(page, element_ref, tab_id)
            if clear_first:
                await locator.fill("", timeout=BROWSER_ACTION_TIMEOUT_MS)
            await locator.fill(text, timeout=BROWSER_ACTION_TIMEOUT_MS)

            if press_enter:
                await locator.press("Enter", timeout=BROWSER_ACTION_TIMEOUT_MS)

            await self._wait_for_stable(page)
            self._update_tab_info(tab_id, page)
            return await self._build_page_state(page, tab_id)

    async def select_option(
        self,
        element_ref: str,
        value: str,
        tab_id: str | None = None,
    ) -> PageState:
        """选择下拉框选项。"""
        async with self._lock:
            self._ensure_started()
            tab_id = tab_id or self._active_tab_id
            if tab_id is None:
                raise BrowserTabNotFoundError("没有活跃的 Tab")
            page = self._resolve_tab(tab_id)

            locator = await self._resolve_element(page, element_ref, tab_id)
            await locator.select_option(value, timeout=BROWSER_ACTION_TIMEOUT_MS)

            await self._wait_for_stable(page)
            self._update_tab_info(tab_id, page)
            return await self._build_page_state(page, tab_id)

    async def scroll(
        self,
        direction: str = "down",
        amount: int = 3,
        tab_id: str | None = None,
    ) -> PageState:
        """滚动页面。direction: up/down。amount: 视口高度的倍数。"""
        async with self._lock:
            self._ensure_started()
            tab_id = tab_id or self._active_tab_id
            if tab_id is None:
                raise BrowserTabNotFoundError("没有活跃的 Tab")
            page = self._resolve_tab(tab_id)

            delta = BROWSER_VIEWPORT_HEIGHT * amount
            if direction == "up":
                delta = -delta

            await page.evaluate(f"window.scrollBy(0, {delta})")
            await self._wait_for_stable(page)
            return await self._build_page_state(page, tab_id)

    async def go_back(self, tab_id: str | None = None) -> PageState:
        """浏览器后退。"""
        async with self._lock:
            self._ensure_started()
            tab_id = tab_id or self._active_tab_id
            if tab_id is None:
                raise BrowserTabNotFoundError("没有活跃的 Tab")
            page = self._resolve_tab(tab_id)

            await page.go_back(wait_until="domcontentloaded")
            await self._wait_for_stable(page)
            self._update_tab_info(tab_id, page)
            return await self._build_page_state(page, tab_id)

    async def go_forward(self, tab_id: str | None = None) -> PageState:
        """浏览器前进。"""
        async with self._lock:
            self._ensure_started()
            tab_id = tab_id or self._active_tab_id
            if tab_id is None:
                raise BrowserTabNotFoundError("没有活跃的 Tab")
            page = self._resolve_tab(tab_id)

            await page.go_forward(wait_until="domcontentloaded")
            await self._wait_for_stable(page)
            self._update_tab_info(tab_id, page)
            return await self._build_page_state(page, tab_id)

    # ── 截图 ──

    async def screenshot(
        self,
        tab_id: str | None = None,
        full_page: bool = False,
    ) -> str:
        """截图并返回文件路径。自动清理过期截图。"""
        async with self._lock:
            self._ensure_started()
            tab_id = tab_id or self._active_tab_id
            if tab_id is None:
                raise BrowserTabNotFoundError("没有活跃的 Tab")
            page = self._resolve_tab(tab_id)

            screenshot_dir = Path(BROWSER_SCREENSHOT_DIR)
            screenshot_dir.mkdir(parents=True, exist_ok=True)

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"screenshot_{tab_id}_{ts}.png"
            filepath = screenshot_dir / filename

            await page.screenshot(path=str(filepath), full_page=full_page)
            logger.info("截图已保存: %s", filepath)

            # 异步清理旧截图（不阻塞返回）
            asyncio.create_task(self._cleanup_old_screenshots())

            return str(filepath)

    # ── 文本提取 ──

    async def get_page_text(
        self,
        selector: str | None = None,
        tab_id: str | None = None,
    ) -> str:
        """获取页面文本内容。"""
        async with self._lock:
            self._ensure_started()
            tab_id = tab_id or self._active_tab_id
            if tab_id is None:
                raise BrowserTabNotFoundError("没有活跃的 Tab")
            page = self._resolve_tab(tab_id)
            return await self._dom_processor.extract_text(page, selector)

    # ── Tab 管理 ──

    async def list_tabs(self) -> list[TabInfo]:
        """列出所有打开的 Tab。"""
        async with self._lock:
            self._ensure_started()
            return [info for _, info in self._tabs.values()]

    async def switch_tab(self, tab_id: str) -> PageState:
        """切换到指定 Tab。"""
        async with self._lock:
            self._ensure_started()
            page = self._resolve_tab(tab_id)
            await page.bring_to_front()
            self._active_tab_id = tab_id
            # 更新访问时间
            if tab_id in self._tabs:
                _, info = self._tabs[tab_id]
                info.is_active = True
                info.last_accessed = datetime.now(timezone.utc)
                # 取消其他 Tab 的活跃状态
                for other_id, (_, other_info) in self._tabs.items():
                    if other_id != tab_id:
                        other_info.is_active = False
            return await self._build_page_state(page, tab_id)

    async def close_tab(self, tab_id: str) -> None:
        """关闭指定 Tab。"""
        async with self._lock:
            self._ensure_started()
            if tab_id not in self._tabs:
                raise BrowserTabNotFoundError(f"Tab 不存在: {tab_id}")

            page, _ = self._tabs[tab_id]
            try:
                await page.close()
            except Exception as exc:
                logger.warning("关闭 Tab 异常: %s", exc)

            del self._tabs[tab_id]
            self._element_map.pop(tab_id, None)

            if self._active_tab_id == tab_id:
                if self._tabs:
                    self._active_tab_id = next(iter(self._tabs))
                else:
                    self._active_tab_id = None

    async def new_tab(self, url: str | None = None) -> TabInfo:
        """创建新 Tab。"""
        async with self._lock:
            self._ensure_started()
            return await self._create_tab_unlocked(url)

    # ── JS 执行 ──

    async def execute_js(
        self,
        expression: str,
        tab_id: str | None = None,
    ) -> str:
        """执行 JavaScript 并返回序列化结果。

        安全：在执行前强制通过 BrowserGuard.check_js() 检查。
        """
        # BrowserGuard JS 安全检查（底层强制，无法绕过）
        if self._browser_guard is not None:
            result = self._browser_guard.check_js(expression)
            if result.action == "block":
                raise BrowserError(f"[BrowserGuard] {result.reason}")

        async with self._lock:
            self._ensure_started()
            tab_id = tab_id or self._active_tab_id
            if tab_id is None:
                raise BrowserTabNotFoundError("没有活跃的 Tab")
            page = self._resolve_tab(tab_id)

            result = await page.evaluate(expression)
            if isinstance(result, (dict, list)):
                return json.dumps(result, ensure_ascii=False, default=str)
            return str(result) if result is not None else ""

    # ── 等待条件 ──

    async def wait_for(
        self,
        condition: str,
        timeout_ms: int = BROWSER_ACTION_TIMEOUT_MS,
        tab_id: str | None = None,
    ) -> bool:
        """等待条件满足。

        condition: 'navigation', 'selector:xxx', 'idle'
        """
        async with self._lock:
            self._ensure_started()
            tab_id = tab_id or self._active_tab_id
            if tab_id is None:
                raise BrowserTabNotFoundError("没有活跃的 Tab")
            page = self._resolve_tab(tab_id)

            try:
                if condition == "navigation":
                    await page.wait_for_load_state(
                        "domcontentloaded", timeout=timeout_ms
                    )
                elif condition.startswith("selector:"):
                    sel = condition[len("selector:"):]
                    await page.wait_for_selector(sel, timeout=timeout_ms)
                elif condition == "idle":
                    await page.wait_for_load_state(
                        "networkidle", timeout=timeout_ms
                    )
                else:
                    logger.warning("未知等待条件: %s", condition)
                    return False
                return True
            except Exception as exc:
                if "timeout" in str(exc).lower():
                    raise BrowserTimeoutError(
                        f"等待条件超时: {condition}"
                    ) from exc
                logger.warning("等待条件失败: %s — %s", condition, exc)
                return False

    # ── 内部方法 ──

    def _ensure_started(self) -> None:
        """确保浏览器已启动。"""
        if self._context is None:
            raise BrowserNotStartedError("BrowserManager 未启动，请先调用 start()")

    def _resolve_tab(self, tab_id: str | None) -> Any:
        """解析 tab_id 到 Page 对象。None = 活跃 Tab。"""
        if tab_id is None:
            tab_id = self._active_tab_id
        if tab_id is None:
            raise BrowserTabNotFoundError("没有活跃的 Tab")
        if tab_id not in self._tabs:
            raise BrowserTabNotFoundError(f"Tab 不存在: {tab_id}")
        page, _ = self._tabs[tab_id]
        return page

    async def _resolve_element(
        self,
        page: Any,
        element_ref: str,
        tab_id: str,
    ) -> Any:
        """解析 element_ref 到 Locator。

        '[3]' -> 查找索引映射中的选择器
        'css:xxx' -> CSS 选择器
        'text:xxx' -> 文本匹配
        """
        # 索引引用: [3] 或 3
        index_match = re.match(r"^\[?(\d+)\]?$", element_ref.strip())
        if index_match:
            idx = int(index_match.group(1))
            tab_map = self._element_map.get(tab_id, {})
            selector = tab_map.get(idx)
            if selector is None:
                raise BrowserElementNotFoundError(
                    f"元素索引 [{idx}] 不存在（当前页面共 {len(tab_map)} 个可交互元素）"
                )
            locator = page.locator(selector).first
            try:
                await locator.wait_for(state="attached", timeout=3000)
            except Exception:
                raise BrowserElementNotFoundError(
                    f"元素 [{idx}] (selector={selector}) 在 DOM 中未找到"
                )
            return locator

        # CSS 选择器引用
        if element_ref.startswith("css:"):
            selector = element_ref[4:]
            locator = page.locator(selector).first
            try:
                await locator.wait_for(state="attached", timeout=3000)
            except Exception:
                raise BrowserElementNotFoundError(
                    f"CSS 选择器未找到元素: {selector}"
                )
            return locator

        # 文本引用
        if element_ref.startswith("text:"):
            text = element_ref[5:]
            locator = page.get_by_text(text, exact=False).first
            try:
                await locator.wait_for(state="attached", timeout=3000)
            except Exception:
                raise BrowserElementNotFoundError(
                    f"文本未找到匹配元素: {text}"
                )
            return locator

        raise BrowserElementNotFoundError(
            f"无法解析元素引用: {element_ref}（支持 [N], css:xxx, text:xxx）"
        )

    async def _build_page_state(self, page: Any, tab_id: str) -> PageState:
        """提取元素 + 文本 + 滚动信息，构建 PageState，缓存元素映射。"""
        elements, metrics = await self._dom_processor.extract_elements(page)
        text_summary = await self._dom_processor.extract_text(page)
        scroll_pos, has_more = await self._dom_processor.get_scroll_info(page)

        # 缓存元素索引映射
        self._element_map[tab_id] = {
            elem.index: elem.selector for elem in elements
        }

        # 判断是否图片密集
        image_heavy = self._should_use_vision(metrics)

        # 视觉理解（图片密集页面 + 开关开启 + router 可用）
        visual_desc: str | None = None
        if image_heavy and BROWSER_VISION_ENABLED and self._router is not None:
            visual_desc = await self._visual_describe(page, tab_id)

        return PageState(
            url=page.url,
            title=await page.title(),
            elements=elements,
            text_summary=text_summary,
            visual_description=visual_desc,
            scroll_position=scroll_pos,
            has_more_below=has_more,
            tab_id=tab_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            is_image_heavy=image_heavy,
        )

    async def _create_tab_unlocked(self, url: str | None = None) -> TabInfo:
        """创建新 Tab（不加锁，供已加锁的方法调用）。"""
        await self._ensure_tab_limit()

        page = await self._context.new_page()
        tab_id = self._generate_tab_id()

        if url:
            try:
                await page.goto(url, wait_until="domcontentloaded")
            except Exception as exc:
                error_msg = str(exc)
                if "timeout" in error_msg.lower():
                    raise BrowserTimeoutError(f"导航超时: {url}") from exc
                raise BrowserNavigationError(f"导航失败: {url} — {error_msg}") from exc

        info = TabInfo(
            tab_id=tab_id,
            url=page.url,
            title=await page.title() if url else "",
            is_active=True,
            last_accessed=datetime.now(timezone.utc),
        )

        # 取消其他 Tab 的活跃状态
        for _, (_, other_info) in self._tabs.items():
            other_info.is_active = False

        self._tabs[tab_id] = (page, info)
        self._active_tab_id = tab_id
        return info

    async def _ensure_tab_limit(self) -> None:
        """如果超过 BROWSER_MAX_TABS，关闭最早访问的 Tab。"""
        while len(self._tabs) >= BROWSER_MAX_TABS:
            # 找到最早访问的 Tab
            oldest_id = min(
                self._tabs,
                key=lambda tid: self._tabs[tid][1].last_accessed,
            )
            logger.info("Tab 数量达上限 (%d)，关闭最早的 Tab: %s", BROWSER_MAX_TABS, oldest_id)
            page, _ = self._tabs[oldest_id]
            try:
                await page.close()
            except Exception:
                pass
            del self._tabs[oldest_id]
            self._element_map.pop(oldest_id, None)
            if self._active_tab_id == oldest_id:
                self._active_tab_id = next(iter(self._tabs), None)

    def _generate_tab_id(self) -> str:
        """生成短唯一 Tab ID，如 'tab_a3f2'。"""
        return f"tab_{uuid.uuid4().hex[:4]}"

    async def _wait_for_stable(self, page: Any) -> None:
        """等待 BROWSER_WAIT_AFTER_ACTION_MS 让 DOM 稳定。"""
        wait_seconds = BROWSER_WAIT_AFTER_ACTION_MS / 1000.0
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)

    def _update_tab_info(self, tab_id: str, page: Any) -> None:
        """更新 Tab 信息（URL、时间）。"""
        if tab_id in self._tabs:
            _, info = self._tabs[tab_id]
            info.url = page.url
            info.last_accessed = datetime.now(timezone.utc)

    async def _save_tab_state(self) -> None:
        """保存 Tab 状态到 data/browser/state.json。"""
        state_path = Path(BROWSER_USER_DATA_DIR).parent / "state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        tabs_state = []
        for tab_id, (page, info) in self._tabs.items():
            tabs_state.append({
                "tab_id": tab_id,
                "url": info.url,
                "title": info.title,
                "is_active": info.is_active,
                "last_accessed": info.last_accessed.isoformat(),
            })

        try:
            state_path.write_text(
                json.dumps({"tabs": tabs_state}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info("Tab 状态已保存到 %s", state_path)
        except Exception as exc:
            logger.warning("保存 Tab 状态失败: %s", exc)

    async def _cleanup_old_screenshots(self) -> None:
        """删除超过 BROWSER_SCREENSHOT_RETAIN_DAYS 天的截图。"""
        screenshot_dir = Path(BROWSER_SCREENSHOT_DIR)
        if not screenshot_dir.exists():
            return

        cutoff = time.time() - BROWSER_SCREENSHOT_RETAIN_DAYS * 86400
        removed = 0
        try:
            for f in screenshot_dir.iterdir():
                if f.is_file() and f.suffix == ".png":
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                        removed += 1
            if removed:
                logger.info("清理了 %d 张过期截图", removed)
        except Exception as exc:
            logger.warning("清理截图异常: %s", exc)

    # ── 视觉理解 ────────────────────────────────────────────────────────────

    def _should_use_vision(self, page_metrics: dict) -> bool:
        """判断当前页面是否需要视觉理解。

        判定逻辑：
        - 图片多且没 alt → 需要视觉
        - 文字少图片多 → 需要视觉
        - 有 canvas 渲染内容 → 需要视觉
        """
        img_count = page_metrics.get("img_count", 0)
        img_with_alt = page_metrics.get("img_with_alt_count", 0)
        text_chars = page_metrics.get("text_node_char_count", 0)
        canvas_count = page_metrics.get("canvas_count", 0)

        if canvas_count > 0:
            return True

        if img_count >= BROWSER_VISION_IMG_THRESHOLD:
            alt_ratio = img_with_alt / img_count if img_count > 0 else 1.0
            if alt_ratio < BROWSER_VISION_ALT_RATIO_THRESHOLD:
                return True

        if text_chars < 500 and img_count >= 3:
            return True

        return False

    async def _visual_describe(self, page: Any, tab_id: str) -> str | None:
        """对当前页面截图并调用视觉模型生成描述。

        优先使用 MiniMax VLM 端点（如果已配置），否则回退到 LLMRouter 视觉 slot。
        缓存策略：同一 tab 在 BROWSER_VISION_CACHE_TTL_SECONDS 内不重复调用。
        失败时返回 None（退回纯 DOM 方案）。
        """
        # 检查缓存
        now = time.time()
        cached = self._vision_cache.get(tab_id)
        if cached is not None:
            cache_ts, cache_desc = cached
            if now - cache_ts < BROWSER_VISION_CACHE_TTL_SECONDS:
                return cache_desc

        if self._vlm_client is None and self._router is None:
            return None

        try:
            import base64
            # 截图为 bytes
            screenshot_bytes = await page.screenshot(type="png")
            b64_data = base64.b64encode(screenshot_bytes).decode("ascii")

            # 加载视觉 prompt
            from src.core.prompt_loader import load_prompt
            vision_prompt = load_prompt("browser_vision_describe")

            description: str | None = None

            # 优先走 MiniMax VLM 端点
            if self._vlm_client is not None:
                try:
                    data_url = f"data:image/png;base64,{b64_data}"
                    description = await self._vlm_client.understand_image(
                        prompt=vision_prompt,
                        image_source=data_url,
                    )
                except Exception as vlm_exc:
                    logger.warning("VLM 端点调用失败，尝试回退 LLMRouter: %s", vlm_exc)

            # 回退到 LLMRouter 视觉 slot
            if description is None and self._router is not None:
                description = await self._router.complete(
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/png",
                                        "data": b64_data,
                                    },
                                },
                                {
                                    "type": "text",
                                    "text": vision_prompt,
                                },
                            ],
                        }
                    ],
                    slot=BROWSER_VISION_SLOT,
                    session_key=f"browser_vision:{tab_id}",
                    origin="browser_manager.visual_describe",
                )

            # 截断到最大长度
            if description and len(description) > BROWSER_VISION_MAX_DESCRIPTION_CHARS:
                description = description[:BROWSER_VISION_MAX_DESCRIPTION_CHARS]

            # 缓存结果
            if description:
                self._vision_cache[tab_id] = (now, description)

            logger.info("视觉描述完成 (tab=%s, %d 字)", tab_id, len(description or ""))
            return description

        except Exception as exc:
            logger.warning("视觉描述失败，退回纯 DOM: %s", exc)
            return None

    # ── 事件发布 ──────────────────────────────────────────────────────────

    async def _publish_event(self, event_type: str, payload: dict) -> None:
        """发布浏览器事件（如果 event_bus 可用）。"""
        if self._event_bus is None:
            return
        try:
            await self._event_bus.publish(event_type, payload)
        except Exception:
            pass  # 事件发布不应阻断主流程
