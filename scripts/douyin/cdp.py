"""Chrome DevTools Protocol 客户端。

通过 WebSocket 与 Chrome 通信，提供页面操作、DOM 操作、输入模拟等能力。
"""

from __future__ import annotations

import base64
import contextlib
import json
import logging
import random
import threading
import time
from typing import Any

import requests
import websockets.sync.client

from .errors import CDPError, ElementNotFoundError
from .selectors import SECOND_VERIFY_CONTAINER
from .stealth import REALISTIC_UA, STEALTH_JS

logger = logging.getLogger(__name__)


class CDPClient:
    """CDP WebSocket 底层客户端。"""

    def __init__(self, ws_url: str) -> None:
        self._ws = websockets.sync.client.connect(ws_url, max_size=50 * 1024 * 1024)
        self._msg_id = 0
        self._ws_lock = threading.Lock()

    def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """发送 CDP 命令并等待响应。"""
        with self._ws_lock:
            self._msg_id += 1
            msg = {"id": self._msg_id, "method": method}
            if params:
                msg["params"] = params
            self._ws.send(json.dumps(msg))

            while True:
                raw = self._ws.recv(timeout=30)
                data = json.loads(raw)
                if data.get("id") == self._msg_id:
                    if "error" in data:
                        raise CDPError(f"CDP 错误: {data['error']}")
                    return data.get("result", {})

    def close(self) -> None:
        """关闭连接。"""
        with contextlib.suppress(Exception):
            self._ws.close()


class Page:
    """CDP 页面操作封装。"""

    def __init__(
        self,
        cdp: CDPClient,
        target_id: str,
        session_id: str,
    ) -> None:
        self._cdp = cdp
        self.target_id = target_id
        self.session_id = session_id
        self._ws = cdp._ws
        self._msg_id_ref = [cdp._msg_id]
        self._pending_events: list[dict[str, Any]] = []
        self._ws_lock = threading.Lock()
        # 身份验证弹窗后台监听
        self._second_verify_proceed = threading.Event()
        self._second_verify_proceed.set()
        self._second_verify_stop = threading.Event()
        self._second_verify_watcher: threading.Thread | None = None

    def start_second_verify_watcher(self) -> None:
        """启动后台监听：检测到 #uc-second-verify 时暂停主线程，等待用户完成验证后自动继续。"""
        if (
            self._second_verify_watcher is not None
            and self._second_verify_watcher.is_alive()
        ):
            return
        self._second_verify_stop.clear()
        self._second_verify_watcher = threading.Thread(
            target=self._run_second_verify_watcher,
            daemon=True,
            name="second-verify-watcher",
        )
        self._second_verify_watcher.start()
        logger.debug("身份验证弹窗后台监听已启动")

    def stop_second_verify_watcher(self) -> None:
        """停止后台监听。"""
        self._second_verify_stop.set()
        self._second_verify_proceed.set()

    def _run_second_verify_watcher(self) -> None:
        """后台线程：轮询检测身份验证弹窗，出现时阻塞主线程直到用户完成验证。"""
        poll_interval = 2.0
        max_wait = 300
        while not self._second_verify_stop.wait(timeout=poll_interval):
            try:
                if not self._has_element_direct(SECOND_VERIFY_CONTAINER):
                    continue
                logger.warning(
                    "检测到身份验证弹窗（接收短信验证码），暂停操作，请完成验证"
                )
                print(
                    "\n[抖音] 需要身份验证：请接收短信验证码，在浏览器中完成验证后程序将自动继续。\n",
                    flush=True,
                )
                self._second_verify_proceed.clear()
                deadline = time.time() + max_wait
                while time.time() < deadline and not self._second_verify_stop.is_set():
                    if not self._has_element_direct(SECOND_VERIFY_CONTAINER):
                        logger.info("身份验证已完成")
                        break
                    time.sleep(2)
                self._second_verify_proceed.set()
            except Exception as e:
                logger.debug("身份验证监听异常: %s", e)
                self._second_verify_proceed.set()

    def _has_element_direct(self, selector: str) -> bool:
        """直接检查元素存在（不经过 _send_session 的等待逻辑，供 watcher 使用）。"""
        result = self._send_session_inner(
            "Runtime.evaluate",
            {
                "expression": f"document.querySelector({json.dumps(selector)}) !== null",
                "returnByValue": True,
            },
        )
        remote = result.get("result", {})
        return bool(remote.get("value"))

    def _send_session_inner(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """内部发送 CDP 命令（无 second_verify 等待）。

        使用 _ws_lock 保护 WebSocket 的 send + recv 过程，
        防止后台 watcher 线程与主线程同时调用 recv 导致 ConcurrencyError。
        """
        with self._ws_lock:
            self._msg_id_ref[0] += 1
            msg_id = self._msg_id_ref[0]
            self._cdp._msg_id = msg_id
            msg: dict[str, Any] = {
                "id": msg_id,
                "method": method,
                "sessionId": self.session_id,
            }
            if params:
                msg["params"] = params
            self._ws.send(json.dumps(msg))
            return self._wait_session(msg_id)

    def _send_session(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """发送 session 级 CDP 命令。"""
        if (
            self._second_verify_watcher is not None
            and threading.current_thread() != self._second_verify_watcher
        ):
            self._second_verify_proceed.wait()
        return self._send_session_inner(method, params)

    def _wait_session(self, msg_id: int, timeout: float = 30.0) -> dict[str, Any]:
        """等待指定 id 的响应。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                remaining = max(0.1, deadline - time.monotonic())
                raw = self._ws.recv(timeout=min(remaining, 5.0))
            except TimeoutError:
                continue

            data = json.loads(raw)
            if data.get("id") == msg_id:
                if "error" in data:
                    raise CDPError(f"CDP 错误: {data['error']}")
                return data.get("result", {})

            # 缓存事件消息
            if "method" in data and "id" not in data:
                self._pending_events.append(data)

        raise CDPError(f"等待响应超时: id={msg_id}")

    # ========== 导航 ==========

    def navigate(self, url: str) -> None:
        """导航到指定 URL。"""
        self._send_session("Page.navigate", {"url": url})

    def wait_for_load(self, timeout: float = 60.0) -> None:
        """等待页面加载完成。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with self._ws_lock:
                    remaining = max(0.1, deadline - time.monotonic())
                    raw = self._ws.recv(timeout=min(remaining, 2.0))
                data = json.loads(raw)
                method = data.get("method")
                if method in ("Page.loadEventFired", "Page.domContentEventFired"):
                    time.sleep(0.5)
                    return
                if "method" in data and "id" not in data:
                    self._pending_events.append(data)
            except TimeoutError:
                continue
        logger.warning("等待页面加载超时")

    def wait_dom_stable(
        self, check_interval: float = 0.5, stable_count: int = 3
    ) -> None:
        """等待 DOM 稳定（连续 N 次检查元素数量不变）。"""
        last_count = -1
        stable = 0
        for _ in range(20):
            count = self.evaluate("document.querySelectorAll('*').length")
            if count == last_count:
                stable += 1
                if stable >= stable_count:
                    return
            else:
                stable = 0
                last_count = count
            time.sleep(check_interval)

    def get_current_url(self) -> str:
        """获取当前页面 URL。"""
        result = self.evaluate("window.location.href")
        return result if isinstance(result, str) else ""

    # ========== JS 执行 ==========

    def evaluate(self, expression: str) -> Any:
        """执行 JS 表达式并返回结果。"""
        result = self._send_session(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": False,
            },
        )
        remote_obj = result.get("result", {})
        if remote_obj.get("type") == "undefined":
            return None
        return remote_obj.get("value")

    def evaluate_async(self, expression: str, timeout: float = 30.0) -> Any:
        """执行异步 JS 表达式（返回 Promise）。"""
        result = self._send_session(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
                "timeout": int(timeout * 1000),
            },
        )
        remote_obj = result.get("result", {})
        return remote_obj.get("value")

    # ========== DOM 操作 ==========

    def has_element(self, selector: str) -> bool:
        """检查元素是否存在。"""
        result = self.evaluate(
            f"document.querySelector({json.dumps(selector)}) !== null"
        )
        return bool(result)

    def wait_for_element(self, selector: str, timeout: float = 10.0) -> None:
        """等待元素出现。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.has_element(selector):
                return
            time.sleep(0.5)
        raise ElementNotFoundError(selector)

    def click_element(self, selector: str) -> None:
        """点击元素（获取坐标后通过 CDP Input 事件点击）。"""
        box = self.evaluate(
            f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (!el) return null;
                const rect = el.getBoundingClientRect();
                return {{
                    x: rect.left + rect.width / 2,
                    y: rect.top + rect.height / 2,
                    width: rect.width,
                    height: rect.height,
                }};
            }})()
            """
        )
        if not box:
            raise ElementNotFoundError(selector)

        x = box["x"] + random.uniform(-2, 2)
        y = box["y"] + random.uniform(-2, 2)

        # 先移动鼠标
        self.mouse_move(x, y)
        time.sleep(random.uniform(0.05, 0.15))

        # 再点击
        self.mouse_click(x, y)

    def click_nth_element(self, selector: str, index: int) -> None:
        """点击第 N 个匹配元素。"""
        box = self.evaluate(
            f"""
            (() => {{
                const els = document.querySelectorAll({json.dumps(selector)});
                const el = els[{index}];
                if (!el) return null;
                const rect = el.getBoundingClientRect();
                return {{
                    x: rect.left + rect.width / 2,
                    y: rect.top + rect.height / 2,
                }};
            }})()
            """
        )
        if not box:
            raise ElementNotFoundError(f"{selector}[{index}]")

        self.mouse_move(box["x"], box["y"])
        time.sleep(random.uniform(0.05, 0.15))
        self.mouse_click(box["x"], box["y"])

    def input_text(self, selector: str, text: str) -> None:
        """向输入框填写文本（全选→删除→逐字输入）。"""
        # 聚焦
        self.click_element(selector)
        time.sleep(0.2)

        # 全选
        self._send_session(
            "Input.dispatchKeyEvent",
            {"type": "keyDown", "key": "a", "code": "KeyA", "modifiers": 2},
        )
        self._send_session(
            "Input.dispatchKeyEvent",
            {"type": "keyUp", "key": "a", "code": "KeyA", "modifiers": 2},
        )

        # 删除
        self._send_session(
            "Input.dispatchKeyEvent",
            {
                "type": "keyDown",
                "key": "Backspace",
                "code": "Backspace",
                "windowsVirtualKeyCode": 8,
            },
        )
        self._send_session(
            "Input.dispatchKeyEvent",
            {
                "type": "keyUp",
                "key": "Backspace",
                "code": "Backspace",
                "windowsVirtualKeyCode": 8,
            },
        )
        time.sleep(0.1)

        # 逐字输入
        for char in text:
            self._send_session(
                "Input.dispatchKeyEvent",
                {"type": "keyDown", "text": char},
            )
            self._send_session(
                "Input.dispatchKeyEvent",
                {"type": "keyUp", "text": char},
            )
            time.sleep(random.uniform(0.1, 0.25))

    def input_content_editable(self, selector: str, text: str) -> None:
        """向 contenteditable 元素填写文本（全选→删除→逐字输入，换行转 Enter）。"""
        # 聚焦
        self.evaluate(
            f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (el) {{ el.focus(); }}
            }})()
            """
        )
        time.sleep(0.2)

        # 全选 + 删除
        self._send_session(
            "Input.dispatchKeyEvent",
            {"type": "keyDown", "key": "a", "code": "KeyA", "modifiers": 2},
        )
        self._send_session(
            "Input.dispatchKeyEvent",
            {"type": "keyUp", "key": "a", "code": "KeyA", "modifiers": 2},
        )
        self._send_session(
            "Input.dispatchKeyEvent",
            {
                "type": "keyDown",
                "key": "Backspace",
                "code": "Backspace",
                "windowsVirtualKeyCode": 8,
            },
        )
        self._send_session(
            "Input.dispatchKeyEvent",
            {
                "type": "keyUp",
                "key": "Backspace",
                "code": "Backspace",
                "windowsVirtualKeyCode": 8,
            },
        )
        time.sleep(0.1)

        # 拟人化逐字输入
        error_chars = "abcdefghijklmnopqrstuvwxyz0123456789"
        next_typo_trigger = random.randint(15, 35)  # 下次触发输错的字符计数
        char_count = 0

        for char in text:
            if char == "\n":
                # 换行前短暂停顿，模拟段落切换思考
                time.sleep(random.uniform(0.3, 0.8))
                # Shift+Enter 换行（抖音评论框）
                self._send_session(
                    "Input.dispatchKeyEvent",
                    {
                        "type": "keyDown",
                        "key": "Enter",
                        "code": "Enter",
                        "windowsVirtualKeyCode": 13,
                        "modifiers": 8,
                    },
                )
                self._send_session(
                    "Input.dispatchKeyEvent",
                    {
                        "type": "keyUp",
                        "key": "Enter",
                        "code": "Enter",
                        "windowsVirtualKeyCode": 13,
                        "modifiers": 8,
                    },
                )
                time.sleep(random.uniform(0.2, 0.5))
                char_count = 0
                next_typo_trigger = random.randint(15, 35)
                continue

            # 每隔一段字符，40% 概率触发"输错-删除"行为
            if char_count >= next_typo_trigger and random.random() < 0.4:
                typo_count = random.randint(1, 2)
                for _ in range(typo_count):
                    typo_char = random.choice(error_chars)
                    self._send_session(
                        "Input.dispatchKeyEvent",
                        {"type": "keyDown", "text": typo_char},
                    )
                    self._send_session(
                        "Input.dispatchKeyEvent",
                        {"type": "keyUp", "text": typo_char},
                    )
                    time.sleep(random.uniform(0.05, 0.12))
                # 停顿后删除错误字符
                time.sleep(random.uniform(0.2, 0.5))
                for _ in range(typo_count):
                    self._send_session(
                        "Input.dispatchKeyEvent",
                        {
                            "type": "keyDown",
                            "key": "Backspace",
                            "code": "Backspace",
                            "windowsVirtualKeyCode": 8,
                        },
                    )
                    self._send_session(
                        "Input.dispatchKeyEvent",
                        {
                            "type": "keyUp",
                            "key": "Backspace",
                            "code": "Backspace",
                            "windowsVirtualKeyCode": 8,
                        },
                    )
                    time.sleep(random.uniform(0.05, 0.1))
                time.sleep(random.uniform(0.1, 0.3))
                char_count = 0
                next_typo_trigger = random.randint(15, 35)

            # 输入正确字符
            self._send_session(
                "Input.dispatchKeyEvent",
                {"type": "keyDown", "text": char},
            )
            self._send_session(
                "Input.dispatchKeyEvent",
                {"type": "keyUp", "text": char},
            )
            char_count += 1

            # 随机字符间隔：50-150ms，5% 概率触发 200ms-1.5s 思考停顿
            if random.random() < 0.05:
                time.sleep(random.uniform(0.2, 1.5))
            else:
                time.sleep(random.uniform(0.05, 0.15))

    def get_element_text(self, selector: str) -> str | None:
        """获取元素文本内容。"""
        return self.evaluate(
            f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                return el ? el.textContent : null;
            }})()
            """
        )

    def get_element_attribute(self, selector: str, attr: str) -> str | None:
        """获取元素属性值。"""
        return self.evaluate(
            f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                return el ? el.getAttribute({json.dumps(attr)}) : null;
            }})()
            """
        )

    def get_elements_count(self, selector: str) -> int:
        """获取匹配元素数量。"""
        result = self.evaluate(
            f"document.querySelectorAll({json.dumps(selector)}).length"
        )
        return result if isinstance(result, int) else 0

    def get_nth_element_text(self, selector: str, index: int) -> str | None:
        """获取第 N 个匹配元素的文本内容。"""
        return self.evaluate(
            f"""
            (() => {{
                const els = document.querySelectorAll({json.dumps(selector)});
                return els[{index}] ? els[{index}].textContent : null;
            }})()
            """
        )

    def click_nth_element(self, selector: str, index: int) -> bool:
        """点击第 N 个匹配元素。

        Returns:
            是否成功点击。
        """
        box = self.evaluate(
            f"""
            (() => {{
                const els = document.querySelectorAll({json.dumps(selector)});
                if (!els[{index}]) return null;
                const el = els[{index}];
                el.scrollIntoView({{block: 'center'}});
                const rect = el.getBoundingClientRect();
                return {{x: rect.left + rect.width / 2, y: rect.top + rect.height / 2}};
            }})()
            """
        )
        if not box:
            return False
        x = box["x"] + random.uniform(-3, 3)
        y = box["y"] + random.uniform(-3, 3)
        self.mouse_move(x, y)
        time.sleep(random.uniform(0.03, 0.08))
        self.mouse_click(x, y)
        return True

    def click_parent_of_nth_element(self, selector: str, index: int) -> bool:
        """点击第 N 个匹配元素的父元素。

        Returns:
            是否成功点击。
        """
        box = self.evaluate(
            f"""
            (() => {{
                const els = document.querySelectorAll({json.dumps(selector)});
                if (!els[{index}]) return null;
                const parent = els[{index}].parentElement;
                if (!parent) return null;
                parent.scrollIntoView({{block: 'center'}});
                const rect = parent.getBoundingClientRect();
                return {{x: rect.left + rect.width / 2, y: rect.top + rect.height / 2}};
            }})()
            """
        )
        if not box:
            return False
        x = box["x"] + random.uniform(-3, 3)
        y = box["y"] + random.uniform(-3, 3)
        self.mouse_move(x, y)
        time.sleep(random.uniform(0.03, 0.08))
        self.mouse_click(x, y)
        return True

    # ========== XPath 操作 ==========

    def has_element_xpath(self, xpath: str) -> bool:
        """通过 XPath 检查元素是否存在。"""
        result = self.evaluate(
            f"""
            (() => {{
                const result = document.evaluate(
                    {json.dumps(xpath)}, document, null,
                    XPathResult.FIRST_ORDERED_NODE_TYPE, null
                );
                return result.singleNodeValue !== null;
            }})()
            """
        )
        return bool(result)

    def click_element_xpath(self, xpath: str, click_parent: bool = False) -> bool:
        """通过 XPath 查找并点击元素。

        Args:
            xpath: XPath 表达式。
            click_parent: 是否点击匹配元素的父元素。

        Returns:
            是否成功点击。
        """
        box = self.evaluate(
            f"""
            (() => {{
                const result = document.evaluate(
                    {json.dumps(xpath)}, document, null,
                    XPathResult.FIRST_ORDERED_NODE_TYPE, null
                );
                let el = result.singleNodeValue;
                if (!el) return null;
                if ({json.dumps(click_parent)}) {{
                    el = el.parentElement;
                    if (!el) return null;
                }}
                el.scrollIntoView({{block: 'center'}});
                const rect = el.getBoundingClientRect();
                return {{x: rect.left + rect.width / 2, y: rect.top + rect.height / 2}};
            }})()
            """
        )
        if not box:
            return False
        x = box["x"] + random.uniform(-3, 3)
        y = box["y"] + random.uniform(-3, 3)
        self.mouse_move(x, y)
        time.sleep(random.uniform(0.03, 0.08))
        self.mouse_click(x, y)
        return True

    # ========== 滚动 ==========

    def scroll_by(self, x: int, y: int) -> None:
        """滚动页面。"""
        self.evaluate(f"window.scrollBy({x}, {y})")

    def scroll_to(self, x: int, y: int) -> None:
        """滚动到指定位置。"""
        self.evaluate(f"window.scrollTo({x}, {y})")

    def scroll_to_bottom(self) -> None:
        """滚动到页面底部。"""
        self.evaluate("window.scrollTo(0, document.body.scrollHeight)")

    def scroll_element_into_view(self, selector: str) -> None:
        """将元素滚动到可视区域。"""
        self.evaluate(
            f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (el) el.scrollIntoView({{behavior: 'smooth', block: 'center'}});
            }})()
            """
        )

    def scroll_element_into_view_slow(self, selector: str) -> None:
        """将元素缓慢滚动到可视区域（复用 scroll_element_into_view + 延迟）。"""
        self.scroll_element_into_view(selector)
        time.sleep(random.uniform(0.8, 1.5))

    def scroll_container_slow(
        self, container_selector: str, delta_y: int | None = None
    ) -> bool:
        """在指定容器内分步缓慢滚动以触发懒加载。用 Python 循环替代大段 JS。

        Args:
            container_selector: 滚动容器的 CSS 选择器。
            delta_y: 滚动像素数，正数向下。None 时使用容器可视高度（clientHeight），
                确保每次滚动一屏以可靠触发懒加载。
        """
        if not self.has_element(container_selector):
            return False
        if delta_y is None:
            h = self.evaluate(
                f"""
                (() => {{
                    const c = document.querySelector({json.dumps(container_selector)});
                    return c ? c.clientHeight : 400;
                }})()
                """
            )
            delta_y = int(h) if isinstance(h, (int, float)) and h > 0 else 400
        # 每步约 1/3 屏，至少 3 次滚动完成一屏
        step = max(150, abs(delta_y) // 3)
        steps = max(3, (abs(delta_y) + step - 1) // step)
        sign = 1 if delta_y >= 0 else -1
        for _ in range(steps):
            self.evaluate(
                f"""
                (() => {{
                    const c = document.querySelector({json.dumps(container_selector)});
                    if (c) c.scrollTop += {step * sign};
                }})()
                """
            )
            time.sleep(random.uniform(0.08, 0.15))  # 80-150ms，流畅不卡顿
        return True

    def scroll_element_into_view_xpath(self, xpath: str) -> bool:
        """将 XPath 匹配的元素滚动到可视区域。返回是否找到并滚动。"""
        result = self.evaluate(
            f"""
            (() => {{
                const r = document.evaluate(
                    {json.dumps(xpath)}, document, null,
                    XPathResult.FIRST_ORDERED_NODE_TYPE, null
                );
                const el = r.singleNodeValue;
                if (el) {{ el.scrollIntoView({{behavior: 'smooth', block: 'center'}}); return true; }}
                return false;
            }})()
            """
        )
        return bool(result)

    def scroll_element_into_view_xpath_slow(
        self, xpath: str, container_selector: str | None = None
    ) -> bool:
        """在指定容器内缓慢滚动以触发加载；若无容器则将目标元素滚入可视区域。"""
        if container_selector and self.has_element(container_selector):
            return self.scroll_container_slow(container_selector)  # 使用容器高度
        return self.scroll_element_into_view_xpath(xpath)

    def scroll_nth_element_into_view(self, selector: str, index: int) -> None:
        """将第 N 个匹配元素滚动到可视区域。"""
        self.evaluate(
            f"""
            (() => {{
                const els = document.querySelectorAll({json.dumps(selector)});
                if (els[{index}]) els[{index}].scrollIntoView(
                    {{behavior: 'smooth', block: 'center'}}
                );
            }})()
            """
        )

    def get_scroll_top(self) -> int:
        """获取当前滚动位置。"""
        result = self.evaluate(
            "window.pageYOffset || document.documentElement.scrollTop"
            " || document.body.scrollTop || 0"
        )
        return int(result) if result else 0

    def get_viewport_height(self) -> int:
        """获取视口高度。"""
        result = self.evaluate("window.innerHeight")
        return int(result) if result else 768

    # ========== 文件上传 ==========

    def set_file_input(self, selector: str, files: list[str]) -> None:
        """设置文件输入框的文件（通过 CDP DOM.setFileInputFiles）。"""
        doc = self._send_session("DOM.getDocument", {"depth": 0})
        root_node_id = doc["root"]["nodeId"]
        result = self._send_session(
            "DOM.querySelector",
            {"nodeId": root_node_id, "selector": selector},
        )
        node_id = result.get("nodeId", 0)
        if node_id == 0:
            raise ElementNotFoundError(selector)
        self._send_session(
            "DOM.setFileInputFiles",
            {"nodeId": node_id, "files": files},
        )

    # ========== 鼠标操作 ==========

    def mouse_move(self, x: float, y: float) -> None:
        """移动鼠标。"""
        self._send_session(
            "Input.dispatchMouseEvent",
            {"type": "mouseMoved", "x": x, "y": y},
        )

    def mouse_click(self, x: float, y: float, button: str = "left") -> None:
        """在指定坐标点击。"""
        self._send_session(
            "Input.dispatchMouseEvent",
            {
                "type": "mousePressed",
                "x": x,
                "y": y,
                "button": button,
                "clickCount": 1,
            },
        )
        self._send_session(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseReleased",
                "x": x,
                "y": y,
                "button": button,
                "clickCount": 1,
            },
        )

    # ========== 键盘操作 ==========

    def type_text(self, text: str, delay_ms: int = 50) -> None:
        """逐字符输入文本。"""
        for char in text:
            self._send_session(
                "Input.dispatchKeyEvent",
                {"type": "keyDown", "text": char},
            )
            self._send_session(
                "Input.dispatchKeyEvent",
                {"type": "keyUp", "text": char},
            )
            if delay_ms > 0:
                time.sleep(delay_ms / 1000.0)

    def press_key(self, key: str) -> None:
        """按下并释放指定键。"""
        key_map = {
            "Enter": {"key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13},
            "ArrowDown": {
                "key": "ArrowDown",
                "code": "ArrowDown",
                "windowsVirtualKeyCode": 40,
            },
            "ArrowUp": {
                "key": "ArrowUp",
                "code": "ArrowUp",
                "windowsVirtualKeyCode": 38,
            },
            "Tab": {"key": "Tab", "code": "Tab", "windowsVirtualKeyCode": 9},
            "Backspace": {
                "key": "Backspace",
                "code": "Backspace",
                "windowsVirtualKeyCode": 8,
            },
            "Escape": {
                "key": "Escape",
                "code": "Escape",
                "windowsVirtualKeyCode": 27,
            },
        }
        info = key_map.get(key, {"key": key, "code": key})
        self._send_session(
            "Input.dispatchKeyEvent",
            {"type": "keyDown", **info},
        )
        self._send_session(
            "Input.dispatchKeyEvent",
            {"type": "keyUp", **info},
        )

    # ========== 反检测 ==========

    def inject_stealth(self) -> None:
        """注入反检测脚本。"""
        self._send_session(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": STEALTH_JS},
        )

    # ========== DOM 辅助 ==========

    def remove_element(self, selector: str) -> None:
        """移除 DOM 元素。"""
        self.evaluate(
            f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (el) el.remove();
            }})()
            """
        )

    def hover_element(self, selector: str) -> None:
        """悬停到元素中心。"""
        box = self.evaluate(
            f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (!el) return null;
                const rect = el.getBoundingClientRect();
                return {{x: rect.left + rect.width / 2, y: rect.top + rect.height / 2}};
            }})()
            """
        )
        if box:
            self.mouse_move(box["x"], box["y"])

    def select_all_text(self, selector: str) -> None:
        """选中输入框内所有文本。"""
        self.evaluate(
            f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (!el) return;
                el.focus();
                el.select ? el.select() : document.execCommand('selectAll');
            }})()
            """
        )

    def dispatch_wheel_event(self, delta_y: float) -> None:
        """触发滚轮事件以激活懒加载。"""
        self.evaluate(
            f"""
            (() => {{
                let target = document.querySelector('.comment-mainContent')
                    || document.querySelector('.video-container')
                    || document.documentElement;
                const event = new WheelEvent('wheel', {{
                    deltaY: {delta_y},
                    deltaMode: 0,
                    bubbles: true,
                    cancelable: true,
                    view: window,
                }});
                target.dispatchEvent(event);
            }})()
            """
        )

    def get_page_source(self) -> str:
        """获取页面 HTML 源码。"""
        result = self.evaluate("document.documentElement.outerHTML")
        return result if isinstance(result, str) else ""

    def get_page_title(self) -> str:
        """获取页面标题。"""
        result = self.evaluate("document.title")
        return result if isinstance(result, str) else ""


class Browser:
    """Chrome 浏览器 CDP 控制器。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 9222) -> None:
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self._cdp: CDPClient | None = None

    def connect(self) -> None:
        """连接到 Chrome DevTools。"""
        resp = requests.get(f"{self.base_url}/json/version", timeout=5)
        resp.raise_for_status()
        info = resp.json()
        ws_url = info["webSocketDebuggerUrl"]
        logger.info("连接到 Chrome: %s", ws_url)
        self._cdp = CDPClient(ws_url)

    def new_page(self, url: str = "about:blank") -> Page:
        """创建新页面。

        先创建空白页，注入 stealth.js 等反检测措施后，再导航到目标 URL。
        确保反检测脚本在首次页面加载前就已生效。
        """
        if not self._cdp:
            self.connect()
        assert self._cdp is not None

        # 先创建空白页（不加载目标 URL，避免 stealth.js 注入前暴露自动化特征）
        result = self._cdp.send("Target.createTarget", {"url": "about:blank"})
        target_id = result["targetId"]

        result = self._cdp.send(
            "Target.attachToTarget",
            {"targetId": target_id, "flatten": True},
        )
        session_id = result["sessionId"]

        page = Page(self._cdp, target_id, session_id)

        # 启用必要的 domain（必须在注入前启用）
        page._send_session("Page.enable")
        page._send_session("DOM.enable")
        page._send_session("Runtime.enable")

        # 注入反检测（在导航前注入，确保首次加载时就生效）
        page.inject_stealth()

        # UA 覆盖
        page._send_session(
            "Emulation.setUserAgentOverride",
            {"userAgent": REALISTIC_UA},
        )

        # 随机 viewport
        page._send_session(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": random.randint(1366, 1920),
                "height": random.randint(768, 1080),
                "deviceScaleFactor": 1,
                "mobile": False,
            },
        )

        # 拒绝权限弹窗
        for perm in ("geolocation", "notifications", "midi", "camera", "microphone"):
            with contextlib.suppress(CDPError):
                self._cdp.send(
                    "Browser.setPermission",
                    {"permission": {"name": perm}, "setting": "denied"},
                )

        # 所有反检测措施就绪后，再导航到目标 URL
        if url and url != "about:blank":
            page.navigate(url)
            page.wait_for_load()

        return page

    def get_existing_page(self) -> Page | None:
        """获取已有页面（优先抖音页面，其次非空白/Chrome 内部页面）。"""
        if not self._cdp:
            self.connect()
        assert self._cdp is not None

        resp = requests.get(f"{self.base_url}/json", timeout=5)
        targets = resp.json()

        douyin_page = None
        other_page = None

        for target in targets:
            if target.get("type") != "page":
                continue
            url = target.get("url", "")
            if url == "about:blank":
                continue

            target_id = target["id"]
            result = self._cdp.send(
                "Target.attachToTarget",
                {"targetId": target_id, "flatten": True},
            )
            session_id = result["sessionId"]
            page = Page(self._cdp, target_id, session_id)
            page._send_session("Page.enable")
            page._send_session("DOM.enable")
            page._send_session("Runtime.enable")
            page.inject_stealth()

            if "douyin.com" in url:
                return page
            elif not url.startswith("chrome://") and other_page is None:
                other_page = page

        return douyin_page or other_page

    def close_page(self, page: Page) -> None:
        """关闭页面。"""
        page.stop_second_verify_watcher()
        if self._cdp:
            with contextlib.suppress(CDPError):
                self._cdp.send("Target.closeTarget", {"targetId": page.target_id})

    def close(self) -> None:
        """关闭连接。"""
        if self._cdp:
            self._cdp.close()
            self._cdp = None


class NetworkCapture:
    """网络请求捕获器，用于捕获指定 API 的请求和响应。

    通过轮询 WebSocket 消息来捕获 CDP Network 域的事件。

    使用方式（单次捕获）：
        with NetworkCapture(page, "aweme/v1/web") as capture:
            page.click_element(PUBLISH_BUTTON)
            request, response = capture.wait_for_capture()

    使用方式（多次捕获，如评论分页）：
        with NetworkCapture(page, "comment/list", multi=True) as capture:
            page.scroll_by(0, 600)
            captured = capture.wait_for_capture_multi(min_count=1)
    """

    def __init__(
        self,
        page: Page,
        url_pattern: str,
        timeout: float = 30.0,
        multi: bool = False,
    ) -> None:
        self._page = page
        self._url_pattern = url_pattern
        self._timeout = timeout
        self._multi = multi
        self._request_data: dict[str, Any] | None = None
        self._response_data: dict[str, Any] | None = None
        self._captured = False
        self._request_id: str | None = None
        self._pending_requests: dict[str, dict[str, Any]] = {}
        self._captured_list: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self._fetched_request_ids: set[str] = set()

    def __enter__(self) -> NetworkCapture:
        """启动网络监听。"""
        self._page._send_session("Network.enable", {})
        self._captured = False
        self._request_data = None
        self._response_data = None
        self._request_id = None
        self._pending_requests = {}
        self._captured_list = []
        self._fetched_request_ids = set()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """停止网络监听。"""
        with contextlib.suppress(CDPError):
            self._page._send_session("Network.disable", {})

    def wait_for_capture(self) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """等待捕获到匹配的请求和响应（单次模式）。

        Returns:
            (request_data, response_data) 或超时返回 (None, None)
        """
        deadline = time.monotonic() + self._timeout

        while time.monotonic() < deadline:
            self._poll_once(deadline)
            if self._captured and self._request_data and self._response_data:
                return self._request_data, self._response_data

        logger.warning("等待捕获超时: %s", self._url_pattern)
        return None, None

    def wait_for_capture_multi(
        self, min_count: int = 1
    ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """等待捕获到至少 min_count 个匹配的请求和响应（多次模式）。

        Returns:
            已捕获的 (request_data, response_data) 列表
        """
        deadline = time.monotonic() + self._timeout

        while time.monotonic() < deadline:
            self._poll_once(deadline)
            if len(self._captured_list) >= min_count:
                return self._captured_list.copy()

        logger.debug(
            "多捕获模式超时，已捕获 %d 条: %s",
            len(self._captured_list),
            self._url_pattern,
        )
        return self._captured_list.copy()

    def get_captured_count(self) -> int:
        """获取已捕获的响应数量（多次模式）。"""
        return len(self._captured_list)

    def poll_for(self, duration: float) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """轮询指定时长，收集期间到达的响应（多次模式）。

        用于滚动后等待新响应到达。
        """
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            self._poll_once(deadline)
        return self._captured_list.copy()

    def _poll_once(self, deadline: float) -> None:
        """轮询一次 WebSocket 消息。"""
        while self._page._pending_events:
            cached_event = self._page._pending_events.pop(0)
            self._dispatch_event(cached_event)
            if (
                not self._multi
                and self._captured
                and self._request_data
                and self._response_data
            ):
                return

        try:
            with self._page._ws_lock:
                remaining = max(0.1, deadline - time.monotonic())
                raw = self._page._ws.recv(timeout=min(remaining, 0.5))
        except TimeoutError:
            return
        except Exception as recv_error:
            logger.warning("接收 WebSocket 消息异常: %s", recv_error)
            return

        if not raw:
            return

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        if "method" in data:
            self._dispatch_event(data)

    def _dispatch_event(self, data: dict[str, Any]) -> None:
        """分发单条 CDP 事件消息。"""
        method = data.get("method")
        params = data.get("params", {})
        session_id = data.get("sessionId")

        if session_id and session_id != self._page.session_id:
            return

        if method == "Network.requestWillBeSent":
            self._handle_request_will_be_sent(params)
        elif method == "Network.responseReceived":
            self._handle_response_received(params)
        elif method == "Network.loadingFinished":
            loading_request_id = params.get("requestId")
            if (
                loading_request_id
                and loading_request_id not in self._fetched_request_ids
            ):
                if loading_request_id in self._pending_requests:
                    self._fetch_response_body(loading_request_id)

    def _handle_request_will_be_sent(self, params: dict[str, Any]) -> None:
        """处理 Network.requestWillBeSent 事件。"""
        request = params.get("request", {})
        url = request.get("url", "")
        request_method = request.get("method", "")
        request_id = params.get("requestId", "")

        if self._url_pattern not in url:
            return

        # 跳过 CORS 预检请求（OPTIONS 没有有效响应体，会污染单次捕获结果）
        if request_method == "OPTIONS":
            return

        request_data = {
            "url": url,
            "method": request_method,
            "headers": request.get("headers", {}),
            "postData": request.get("postData"),
        }
        self._pending_requests[request_id] = request_data

        if not self._multi and not self._captured:
            self._request_id = request_id
            self._request_data = request_data
            logger.info("捕获到请求: %s (requestId=%s)", url, request_id)

    def _handle_response_received(self, params: dict[str, Any]) -> None:
        """处理 Network.responseReceived 事件。"""
        request_id = params.get("requestId", "")
        response = params.get("response", {})
        url = response.get("url", "")

        if self._url_pattern not in url or request_id not in self._pending_requests:
            return

        logger.info("捕获到响应: %s (status=%s)", url[:80], response.get("status"))
        self._fetch_response_body(request_id)

    def _fetch_response_body(self, request_id: str) -> None:
        """获取响应体内容（含重试）。"""
        if request_id in self._fetched_request_ids:
            return
        request_data = self._pending_requests.get(request_id)
        if not request_data:
            return

        max_retries = 3
        for attempt in range(max_retries):
            try:
                body_result = self._page._send_session(
                    "Network.getResponseBody",
                    {"requestId": request_id},
                )
                body = body_result.get("body", "")
                base64_encoded = body_result.get("base64Encoded", False)
                if base64_encoded:
                    body = base64.b64decode(body).decode("utf-8", errors="replace")
                response_data = {"body": body, "base64Encoded": False}
                self._fetched_request_ids.add(request_id)

                if self._multi:
                    self._captured_list.append((request_data, response_data))
                    logger.info("多捕获: 已累积 %d 条", len(self._captured_list))
                else:
                    self._response_data = response_data
                    self._captured = True
                    logger.info("成功获取响应体 (attempt=%d)", attempt + 1)
                return
            except CDPError as fetch_error:
                if attempt < max_retries - 1:
                    logger.info(
                        "获取响应体暂未就绪 (attempt=%d/%d): %s",
                        attempt + 1, max_retries, fetch_error,
                    )
                    time.sleep(0.5)
                else:
                    logger.warning(
                        "获取响应体最终失败 (requestId=%s): %s",
                        request_id, fetch_error,
                    )
