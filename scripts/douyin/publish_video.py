"""普通视频发布：上传视频、填写标题/简介、设置封面/标签/热点，等待用户确认后发布。

通过 CDP 模拟在抖音创作者中心发布普通视频内容。
发布页面：https://creator.douyin.com/creator-micro/content/post/video
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from .cdp import Page
from .errors import PublishError, UploadTimeoutError
from .human import sleep_random
from .urls import PUBLISH_VIDEO_URL

logger = logging.getLogger(__name__)

# 视频格式限制
VIDEO_MAX_SIZE_BYTES = 16 * 1024 * 1024 * 1024  # 16GB
VIDEO_MAX_DURATION_SECONDS = 60 * 60             # 60 分钟
VIDEO_MAX_WIDTH = 3840
VIDEO_MAX_HEIGHT = 2160

# 可见性选项映射（文字 → radio 值）
VISIBILITY_MAP = {
    "公开": "公开",
    "好友可见": "好友可见",
    "仅自己可见": "仅自己可见",
}


def check_video_format(video_path: str) -> dict:
    """检测普通视频格式是否符合要求（大小/时长/分辨率）。

    Args:
        video_path: 视频文件路径。

    Returns:
        包含 valid、errors、info 的字典。
    """
    errors = []
    info: dict = {}

    if not os.path.isfile(video_path):
        return {"valid": False, "errors": [f"文件不存在: {video_path}"], "info": {}}

    # 文件大小
    file_size = os.path.getsize(video_path)
    info["file_size_bytes"] = file_size
    info["file_size_gb"] = round(file_size / (1024 ** 3), 2)
    if file_size > VIDEO_MAX_SIZE_BYTES:
        errors.append(f"文件大小 {info['file_size_gb']}GB 超过限制 16GB")

    # 时长和分辨率（需要 opencv）
    try:
        import cv2

        cap = cv2.VideoCapture(video_path)
        if cap.isOpened():
            fps = cap.get(cv2.CAP_PROP_FPS) or 30
            frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            duration = frame_count / fps if fps > 0 else 0
            cap.release()

            info["duration_seconds"] = round(duration, 1)
            info["width"] = width
            info["height"] = height
            info["fps"] = round(fps, 1)

            if duration > VIDEO_MAX_DURATION_SECONDS:
                errors.append(
                    f"视频时长 {round(duration / 60, 1)} 分钟超过限制 60 分钟"
                )
            if width > VIDEO_MAX_WIDTH or height > VIDEO_MAX_HEIGHT:
                errors.append(
                    f"视频分辨率 {width}x{height} 超过最高支持的 4K (3840x2160)"
                )
        else:
            errors.append("无法读取视频文件，请确认文件格式正确")
    except ImportError:
        info["note"] = "opencv-python 未安装，跳过时长/分辨率检测"

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "info": info,
    }


def fill_publish_video(
    page: Page,
    title: str,
    content: str,
    video_file: str,
    topics: list[str] | None = None,
    location: str = "",
    product: str = "",
    hotspot: str = "",
    visibility: str = "公开",
    allow_save: bool = True,
) -> dict:
    """填写普通视频发布表单（不点击发布按钮）。

    普通视频不需要用户指定封面，上传完成后自动使用官方生成的封面。

    Args:
        page: CDP 页面对象。
        title: 作品标题（必填，不超过30字）。
        content: 作品简介（不超过800字，纯文字）。
        video_file: 视频文件绝对路径。
        topics: 话题标签列表，会自动追加到简介末尾（格式：#话题1 #话题2）。
        location: 位置标签，空字符串表示不添加。
        product: 同款好物标签，空字符串表示不添加。
        hotspot: 关联热点词，空字符串表示不关联。
        visibility: 可见范围（公开/好友可见/仅自己可见）。
        allow_save: 是否允许保存。

    Returns:
        操作结果字典，包含 success 字段。
    """
    if not os.path.isfile(video_file):
        return {"success": False, "error": f"视频文件不存在: {video_file}"}

    # 将话题标签追加到简介末尾（格式：#话题1 #话题2）
    full_content = content
    if topics:
        topic_str = " ".join(f"#{t.lstrip('#')}" for t in topics)
        full_content = f"{content}\n{topic_str}" if content else topic_str

    logger.info("导航到普通视频发布页")
    page.navigate(PUBLISH_VIDEO_URL)
    page.wait_for_load()
    page.wait_dom_stable()
    sleep_random(4000, 5000)

    # 上传前清空标题和简介，避免残留内容干扰
    _clear_title_and_content(page)

    # 1. 上传视频并等待处理完成
    _upload_video(page, video_file)

    # 2. 填写标题
    _fill_title(page, title)

    # 3. 填写简介（含话题tag）
    if full_content:
        _fill_content(page, full_content)

    # 4. 设置封面（视频上传完成后，使用官方生成封面）
    _set_official_cover(page)

    # 5. 添加位置标签
    if location:
        _add_location_tag(page, location)

    # 6. 添加同款好物标签
    if product:
        _add_product_tag(page, product)

    # 7. 关联热点
    if hotspot:
        _add_hotspot(page, hotspot)

    # 8. 设置可见范围
    _set_visibility(page, visibility)

    # 9. 设置保存权限
    _set_allow_save(page, allow_save)

    logger.info("普通视频表单填写完成，等待用户确认发布")
    return {"success": True, "message": "表单填写完成，请确认后调用 click-publish 发布"}


def _clear_title_and_content(page: Page) -> None:
    """上传前清空标题输入框和简介编辑器，避免残留内容干扰。"""
    logger.info("清空标题和简介")
    # 清空标题 input
    page.evaluate(
        """
        (() => {
            const titleSelectors = [
                'input[placeholder="添加作品标题"]',
                'input[placeholder*="标题"]',
                'input.semi-input',
            ];
            for (const sel of titleSelectors) {
                const input = document.querySelector(sel);
                if (!input) continue;
                const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value'
                ).set;
                nativeInputValueSetter.call(input, '');
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.dispatchEvent(new Event('change', { bubbles: true }));
                break;
            }
        })()
        """
    )
    # 清空简介编辑器（Slate.js 富文本）：必须通过 CDP 键盘事件模拟操作，
    # 直接操作 DOM 无法同步 Slate 内部数据模型，会导致字数统计不更新、缓存内容残留。
    editor_selectors = [
        'div.zone-container[contenteditable="true"]',
        'div.editor-kit-container[contenteditable="true"]',
        'div.zone-container div[contenteditable="true"]',
        'div.editor-kit-container div[contenteditable="true"]',
        'div[data-slate-editor="true"][contenteditable="true"]',
    ]
    for selector in editor_selectors:
        found = page.evaluate(
            f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (!el) return false;
                el.focus();
                return true;
            }})()
            """
        )
        if not found:
            continue
        logger.info("找到简介编辑器: %s", selector)
        sleep_random(150, 300)
        # 通过 CDP 键盘事件模拟 Ctrl+A 全选 + Backspace 删除（macOS 用 Meta）
        is_mac = page.evaluate("navigator.platform").startswith("Mac") if page.evaluate("navigator.platform") else False
        # modifiers: 1=Alt, 2=Ctrl, 4=Meta, 8=Shift
        modifier_flag = 4 if is_mac else 2
        modifier_key = "Meta" if is_mac else "Control"
        for _attempt in range(3):
            # 全选: modifier + A
            page._send_session("Input.dispatchKeyEvent", {
                "type": "keyDown", "key": modifier_key, "code": f"{modifier_key}Left",
                "windowsVirtualKeyCode": 91 if is_mac else 17, "modifiers": modifier_flag,
            })
            page._send_session("Input.dispatchKeyEvent", {
                "type": "keyDown", "key": "a", "code": "KeyA",
                "windowsVirtualKeyCode": 65, "modifiers": modifier_flag,
            })
            page._send_session("Input.dispatchKeyEvent", {
                "type": "keyUp", "key": "a", "code": "KeyA",
                "windowsVirtualKeyCode": 65, "modifiers": modifier_flag,
            })
            page._send_session("Input.dispatchKeyEvent", {
                "type": "keyUp", "key": modifier_key, "code": f"{modifier_key}Left",
                "windowsVirtualKeyCode": 91 if is_mac else 17,
            })
            sleep_random(80, 150)
            # Backspace 删除选中内容
            page.press_key("Backspace")
            sleep_random(150, 300)
            # 检查是否清空
            remaining = page.evaluate(
                f"""
                (() => {{
                    const el = document.querySelector({json.dumps(selector)});
                    if (!el) return '';
                    return el.textContent.replace(/\\u200b/g, '').trim();
                }})()
                """
            ) or ""
            if not remaining:
                logger.info("简介编辑器已清空")
                break
            logger.warning("简介编辑器仍有残留内容（%d字符），第%d次重试", len(remaining), _attempt + 1)
        break


def _upload_video(page: Page, video_file: str) -> None:
    """上传视频文件并等待处理完成。

    上传 input 的查找策略：在类名前缀为 phone-screen 且含「视频」子元素的容器内，
    找到类名前缀为 upload-btn-input 的 input 元素，通过 backendNodeId 注入文件。
    上传后通过检测 preview-card-control 内是否含「取消上传」来判断上传是否完成。
    """
    abs_path = str(Path(video_file).resolve())
    logger.info("上传视频: %s", abs_path)

    # 在 phone-screen 容器（含「视频」子元素）内查找 upload-btn-input
    backend_node_id = page.evaluate(
        """
        (() => {
            const containers = document.querySelectorAll('[class*="phone-screen"]');
            for (const container of containers) {
                if (!container.textContent.includes('视频')) continue;
                const input = container.querySelector('input[class*="upload-btn-input"]');
                if (input) {
                    // 返回一个可序列化的标识：用 xpath 定位
                    return true;
                }
            }
            return false;
        })()
        """
    )

    if not backend_node_id:
        # 降级：直接用类名选择器
        logger.warning("未找到 phone-screen 容器内的上传 input，降级使用全局选择器")
        video_input_selector = "input[class*='upload-btn-input']"
        if not page.has_element(video_input_selector):
            raise PublishError("未找到视频上传入口（upload-btn-input）")
        page.set_file_input(video_input_selector, [abs_path])
    else:
        # 通过 JS 找到目标 input 并用 CDP DOM.setFileInputFiles 注入文件
        injected = page.evaluate(
            f"""
            (() => {{
                const containers = document.querySelectorAll('[class*="phone-screen"]');
                for (const container of containers) {{
                    if (!container.textContent.includes('视频')) continue;
                    const input = container.querySelector('input[class*="upload-btn-input"]');
                    if (input) {{
                        // 标记该 input 以便 Python 侧通过 selector 找到它
                        input.setAttribute('data-dy-upload-target', 'video');
                        return true;
                    }}
                }}
                return false;
            }})()
            """
        )
        if injected:
            page.set_file_input("input[data-dy-upload-target='video']", [abs_path])
        else:
            raise PublishError("未找到 phone-screen 容器内的视频上传 input")

    sleep_random(6000, 7000)

    # 延迟 3 秒再开始检测视频是否处理完成
    time.sleep(3)
    _wait_for_video_ready(page)
    logger.info("视频上传处理完成")


def _wait_for_video_ready(page: Page, timeout: float = 600.0) -> None:
    """等待视频上传处理完成。

    检测逻辑：如果类名前缀为 preview-card-control 的元素内含有
    text 为「取消上传」的子元素，说明视频还在上传中，需要继续等待。
    """
    deadline = time.monotonic() + timeout
    logger.info("等待视频处理完成（最多 %.0f 秒）...", timeout)

    while time.monotonic() < deadline:
        still_uploading = page.evaluate(
            """
            (() => {
                const controls = document.querySelectorAll('[class*="preview-card-control"]');
                for (const ctrl of controls) {
                    if (ctrl.textContent.includes('取消上传')) {
                        return true;
                    }
                }
                return false;
            })()
            """
        )
        if not still_uploading:
            return
        time.sleep(2)

    raise UploadTimeoutError("视频处理超时（10分钟），请检查视频文件是否正常")


def _fill_title(page: Page, title: str) -> None:
    """填写作品标题。

    抖音视频标题使用 semi-input（React 受控组件），需要通过 JS 模拟原生 input 事件
    才能触发 React 的 onChange，否则 value 不会被框架感知。
    """
    logger.info("填写标题: %s", title[:30])

    filled = page.evaluate(
        f"""
        (() => {{
            // 优先匹配 placeholder="添加作品标题" 的 input
            const selectors = [
                'input[placeholder="添加作品标题"]',
                'input[placeholder*="标题"]',
                'input.semi-input',
            ];
            for (const sel of selectors) {{
                const input = document.querySelector(sel);
                if (!input) continue;
                // 聚焦
                input.focus();
                // 通过 React 内部 setter 设置 value，触发 onChange
                const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value'
                ).set;
                nativeInputValueSetter.call(input, {repr(title)});
                input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                return true;
            }}
            return false;
        }})()
        """
    )

    if filled:
        sleep_random(2300, 2500)
    else:
        logger.warning("未找到标题输入框，跳过")


def _fill_content(page: Page, content: str) -> None:
    """填写作品简介（含话题tag）。

    使用 CDP input_content_editable 逐字输入。调用前先等待足够时间确保
    WebSocket 无并发冲突，并加入重试机制。
    """
    logger.info("填写简介: %d 字", len(content))

    content_selector = (
        "div[contenteditable='true'][placeholder*='简介'], "
        "div[contenteditable='true'][class*='desc'], "
        "div[contenteditable='true'][class*='editor']"
    )
    if not page.has_element(content_selector):
        content_selector = "div[contenteditable='true']"

    if not page.has_element(content_selector):
        logger.warning("未找到简介编辑器，跳过")
        return

    # 等待足够时间确保 WebSocket 无并发冲突
    time.sleep(3)

    max_retries = 3
    for attempt in range(max_retries):
        try:
            page.input_content_editable(content_selector, content + " ")
            sleep_random(4300, 4500)
            return
        except Exception as exc:
            if attempt < max_retries - 1:
                logger.warning(
                    "填写简介失败（第%d次），等待后重试: %s", attempt + 1, exc
                )
                time.sleep(3)
            else:
                logger.error("填写简介失败（已重试%d次）: %s", max_retries, exc)
                raise


def _set_official_cover(page: Page) -> None:
    """使用官方生成的封面。

    普通视频不需要用户指定封面，上传完成后直接使用官方生成的封面。
    流程：
    1. 点击「选择封面」按钮（类名前缀为 title 且 text 包含「选择封面」）
    2. 在弹窗（类名为 dy-creator-content-modal-content 且 text 包含「封面」）内
       直接点击「完成」按钮（类名为 semi-button 且 text 为「完成」）
    """
    logger.info("设置官方生成封面")

    # 点击「选择封面」按钮
    cover_btn_result = page.evaluate(
        """
        (() => {
            const els = document.querySelectorAll('[class*="title"]');
            for (const el of els) {
                if (el.textContent.includes('选择封面')) {
                    const rect = el.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        el.click();
                        return true;
                    }
                }
            }
            return false;
        })()
        """
    )
    if not cover_btn_result:
        logger.warning("未找到「选择封面」按钮，跳过封面设置")
        return

    sleep_random(4000, 5000)

    # 在封面弹窗内点击「完成」按钮
    done_result = page.evaluate(
        """
        (() => {
            const modals = document.querySelectorAll('.dy-creator-content-modal-content');
            for (const modal of modals) {
                if (modal.textContent.includes('封面')) {
                    const btns = modal.querySelectorAll('button.semi-button');
                    for (const btn of btns) {
                        if (btn.textContent.trim() === '完成') {
                            const rect = btn.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0) {
                                btn.click();
                                return true;
                            }
                        }
                    }
                }
            }
            return false;
        })()
        """
    )
    if done_result:
        sleep_random(2500, 2800)
        logger.info("已使用官方生成封面")
    else:
        logger.warning("未找到封面弹窗内的「完成」按钮")
        page.press_key("Escape")


def _add_location_tag(page: Page, location: str) -> None:
    """添加位置标签。

    与图文发布逻辑一致：
    1. 找到 class 为 semi-select-selection 且子元素含「输入地理位置」文案的元素
    2. 找到该元素下的 input 并聚焦
    3. 输入位置名称，等待搜索结果加载
    4. 在 .semi-select-option-list 容器内点击第一条 .semi-select-option
    """
    logger.info("添加位置标签: %s", location)

    input_focused = page.evaluate(
        """
        (() => {
            const selections = document.querySelectorAll('.semi-select-selection');
            for (const sel of selections) {
                if (sel.textContent.includes('输入地理位置')) {
                    const inp = sel.querySelector('input');
                    if (inp) {
                        inp.click();
                        inp.focus();
                        return true;
                    }
                    sel.click();
                    return 'clicked-selection';
                }
            }
            return false;
        })()
        """
    )

    if not input_focused:
        logger.warning(
            "未找到位置搜索输入框（semi-select-selection 含「输入地理位置」），跳过"
        )
        return

    logger.info("已聚焦位置搜索输入框，开始输入")
    sleep_random(2300, 2500)

    page.type_text(location, delay_ms=80)
    sleep_random(7000, 10500)

    option_clicked = page.evaluate(
        """
        (() => {
            const optionList = document.querySelector('.semi-select-option-list');
            if (!optionList) return false;
            const firstOption = optionList.querySelector('.semi-select-option');
            if (firstOption) {
                const rect = firstOption.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {
                    firstOption.click();
                    return firstOption.textContent.trim().slice(0, 20);
                }
            }
            return false;
        })()
        """
    )

    if option_clicked:
        logger.info("已选择位置: %s（选中项: %s）", location, option_clicked)
        sleep_random(2500, 2800)
    else:
        logger.warning("未找到位置联想结果，跳过")


def _add_product_tag(page: Page, product: str) -> None:
    """添加同款好物标签（标记万物）。

    与图文发布逻辑一致：
    1. 找到 class 为 semi-select 且子元素文案含「位置」的下拉选框，点击打开 dropdown
    2. 在 dropdown 中找到 class 为 semi-select-option 且子元素文案含「标记万物」的选项并点击
    3. 找到 class 为 semi-input 且 placeholder 含「标记的物品」的 input 并聚焦
    4. 输入好物名称，等待搜索结果加载
    5. 找到类名前缀为 dropdown 且包含「标记同款好物」文案的容器，点击其第一个类名前缀为 option 的子元素
    """
    logger.info("添加同款好物标签: %s", product)

    dropdown_clicked = page.evaluate(
        """
        (() => {
            const semiSelects = document.querySelectorAll('.semi-select');
            for (const sel of semiSelects) {
                if (sel.textContent.includes('位置')) {
                    const rect = sel.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        sel.click();
                        return true;
                    }
                }
            }
            return false;
        })()
        """
    )

    if not dropdown_clicked:
        logger.warning("未找到「位置」下拉选框，跳过同款好物标签")
        return

    sleep_random(3500, 4800)

    option_selected = page.evaluate(
        """
        (() => {
            const options = document.querySelectorAll('.semi-select-option');
            for (const opt of options) {
                if (opt.textContent.includes('标记万物')) {
                    const rect = opt.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        opt.click();
                        return true;
                    }
                }
            }
            return false;
        })()
        """
    )

    if not option_selected:
        logger.warning("未找到「标记万物」选项，跳过同款好物标签")
        return

    sleep_random(2500, 2800)

    input_focused = page.evaluate(
        """
        (() => {
            const inputs = document.querySelectorAll('input.semi-input');
            for (const inp of inputs) {
                const ph = inp.getAttribute('placeholder') || '';
                if (ph.includes('标记的物品')) {
                    const rect = inp.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        inp.click();
                        inp.focus();
                        return true;
                    }
                }
            }
            return false;
        })()
        """
    )

    if not input_focused:
        logger.warning(
            "未找到同款好物搜索输入框（semi-input 含「标记的物品」），跳过"
        )
        return

    logger.info("已聚焦好物搜索输入框，开始输入")
    sleep_random(2300, 2500)

    page.type_text(product, delay_ms=80)
    sleep_random(7000, 10500)

    option_clicked = page.evaluate(
        """
        (() => {
            const dropdowns = document.querySelectorAll('[class*="dropdown"]');
            for (const dropdown of dropdowns) {
                if (dropdown.textContent.includes('标记同款好物')) {
                    const firstOption = dropdown.querySelector('[class*="option"]');
                    if (firstOption) {
                        const rect = firstOption.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            firstOption.click();
                            return firstOption.textContent.trim().slice(0, 20);
                        }
                    }
                }
            }
            return false;
        })()
        """
    )

    if option_clicked:
        logger.info("已选择同款好物: %s（选中项: %s）", product, option_clicked)
        sleep_random(2500, 2800)
    else:
        logger.warning("未找到同款好物联想结果，跳过")


def _add_hotspot(page: Page, hotspot: str) -> None:
    """关联热点话题。

    与图文发布逻辑一致：
    1. 找到 class 为 semi-select-selection 且子元素含「输入热点词」文案的元素
    2. 找到该元素下的 input 并聚焦
    3. 输入热点词，等待搜索结果加载
    4. 在 .semi-select-option-list 容器内点击第一条 .semi-select-option
    """
    logger.info("关联热点: %s", hotspot)

    input_focused = page.evaluate(
        """
        (() => {
            const selections = document.querySelectorAll('.semi-select-selection');
            for (const sel of selections) {
                if (sel.textContent.includes('输入热点词')) {
                    const inp = sel.querySelector('input');
                    if (inp) {
                        inp.click();
                        inp.focus();
                        return true;
                    }
                    sel.click();
                    return 'clicked-selection';
                }
            }
            return false;
        })()
        """
    )

    if not input_focused:
        logger.warning(
            "未找到热点搜索输入框（semi-select-selection 含「输入热点词」），跳过"
        )
        return

    logger.info("已聚焦热点搜索输入框，开始输入")
    sleep_random(2300, 2500)

    page.type_text(hotspot, delay_ms=80)
    sleep_random(7000, 9000)

    option_clicked = page.evaluate(
        """
        (() => {
            const optionList = document.querySelector('.semi-select-option-list');
            if (!optionList) return false;
            const firstOption = optionList.querySelector('.semi-select-option');
            if (firstOption) {
                const rect = firstOption.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {
                    firstOption.click();
                    return firstOption.textContent.trim().slice(0, 20);
                }
            }
            return false;
        })()
        """
    )

    if option_clicked:
        logger.info("已关联热点: %s（选中项: %s）", hotspot, option_clicked)
        sleep_random(2500, 2800)
    else:
        logger.warning("未找到热点联想结果，跳过")


def _set_visibility(page: Page, visibility: str) -> None:
    """设置可见范围（与图文发布逻辑一致）。"""
    if visibility == "公开":
        return

    logger.info("设置可见范围: %s", visibility)

    page.evaluate(
        f"""
        (() => {{
            const labels = document.querySelectorAll('label, div[class*="radio"], span[class*="radio"]');
            for (const label of labels) {{
                if (label.textContent.trim().includes({json.dumps(visibility)})) {{
                    const input = label.querySelector('input[type="radio"]') || label;
                    input.click();
                    return true;
                }}
            }}
            const radios = document.querySelectorAll('input[type="radio"]');
            for (const radio of radios) {{
                const parent = radio.closest('label') || radio.parentElement;
                if (parent && parent.textContent.trim().includes({json.dumps(visibility)})) {{
                    radio.click();
                    return true;
                }}
            }}
            return false;
        }})()
        """
    )
    sleep_random(2200, 2400)


def _set_allow_save(page: Page, allow_save: bool) -> None:
    """设置保存权限（与图文发布逻辑一致）。

    实际 HTML 结构使用 checkbox + label，通过找到包含「保存权限」文案的区域，
    再找对应 value 的 checkbox input 并点击其父 label。
    """
    target_value = "1" if allow_save else "0"
    target_text = "允许" if allow_save else "不允许"
    logger.info("设置保存权限: %s", target_text)

    clicked = page.evaluate(
        f"""
        (() => {{
            // 策略1：找包含「保存权限」文案的区域，再找对应 value 的 checkbox
            const allEls = document.querySelectorAll('*');
            for (const el of allEls) {{
                if (el.children.length === 0 && el.textContent.trim() === '保存权限') {{
                    let parent = el.parentElement;
                    for (let i = 0; i < 8 && parent; i++) {{
                        const inputs = parent.querySelectorAll('input[type="checkbox"]');
                        for (const inp of inputs) {{
                            if (inp.value === {repr(target_value)}) {{
                                const label = inp.closest('label') || inp.parentElement;
                                if (label) {{
                                    label.click();
                                }} else {{
                                    inp.click();
                                }}
                                return 'by-value';
                            }}
                        }}
                        parent = parent.parentElement;
                    }}
                }}
            }}

            // 策略2：找 class 含 radio 的 label，通过 span 文案匹配
            const labels = document.querySelectorAll('label[class*="radio"]');
            for (const label of labels) {{
                const span = label.querySelector('span');
                if (span && span.textContent.trim().startsWith({repr(target_text)})) {{
                    const rect = label.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {{
                        label.click();
                        return 'by-label-text';
                    }}
                }}
            }}

            // 策略3：找 value 对应的 checkbox input，直接点击
            const checkboxes = document.querySelectorAll('input[type="checkbox"][class*="radio"]');
            for (const cb of checkboxes) {{
                if (cb.value === {repr(target_value)}) {{
                    const label = cb.closest('label') || cb.parentElement;
                    if (label) {{
                        label.click();
                    }} else {{
                        cb.click();
                    }}
                    return 'by-checkbox-value';
                }}
            }}

            return false;
        }})()
        """
    )

    if clicked:
        logger.info("已设置保存权限: %s（策略: %s）", target_text, clicked)
    else:
        logger.warning("未找到保存权限选项「%s」，跳过", target_text)
    sleep_random(2200, 2300)
