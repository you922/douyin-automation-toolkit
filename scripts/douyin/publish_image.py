"""图文发布：上传图片、填写标题/描述、选择音乐/标签/热点，等待用户确认后发布。

通过 CDP 模拟在抖音创作者中心发布图文内容。
发布页面：https://creator.douyin.com/creator-micro/content/post/image
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
from .urls import PUBLISH_IMAGE_URL, UPLOAD_IMAGE_URL

logger = logging.getLogger(__name__)

# 图文最多图片数量
IMAGE_MAX_COUNT = 35


def fill_publish_image(
    page: Page,
    title: str,
    content: str,
    images: list[str],
    topics: list[str] | None = None,
    music: str = "",
    location: str = "",
    product: str = "",
    hotspot: str = "",
    visibility: str = "公开",
    allow_save: bool = True,
) -> dict:
    """填写图文发布表单（不点击发布按钮）。

    Args:
        page: CDP 页面对象。
        title: 作品标题（必填，不超过20字）。
        content: 作品描述（必填，不超过800字，纯文字）。
        images: 图片文件路径列表（必填，最多35张）。
        topics: 话题标签列表，会自动追加到描述末尾（格式：#话题1 #话题2）。
        music: 背景音乐名称，空字符串表示不选择。
        location: 位置标签，空字符串表示不添加。
        product: 同款好物标签，空字符串表示不添加。
        hotspot: 关联热点词，空字符串表示不关联。
        visibility: 可见范围（公开/好友可见/仅自己可见）。
        allow_save: 是否允许保存。

    Returns:
        操作结果字典，包含 success 字段。
    """
    if not images:
        return {"success": False, "error": "图片不能为空"}

    # 验证图片文件存在
    valid_images = []
    for img_path in images[:IMAGE_MAX_COUNT]:
        if os.path.isfile(img_path):
            valid_images.append(img_path)
        else:
            logger.warning("图片文件不存在，跳过: %s", img_path)

    if not valid_images:
        return {"success": False, "error": "没有有效的图片文件"}

    if len(images) > IMAGE_MAX_COUNT:
        logger.warning(
            "图片数量 %d 超过限制 %d，截取前 %d 张",
            len(images),
            IMAGE_MAX_COUNT,
            IMAGE_MAX_COUNT,
        )

    # 将话题标签追加到描述末尾（格式：#话题1 #话题2）
    full_content = content
    if topics:
        topic_str = " ".join(f"#{t.lstrip('#')}" for t in topics)
        full_content = f"{content}\n{topic_str}" if content else topic_str

    # 1. 导航到上传页，上传图片（上传完成后页面会自动跳转到发布页）
    logger.info("导航到图文上传页，上传图片")
    _upload_images(page, valid_images)

    # 2. 填写标题
    _fill_title(page, title)

    # 3. 填写描述（含话题tag）
    if full_content:
        _fill_content(page, full_content)

    # 4. 选择音乐
    if music:
        _select_music(page, music)
        sleep_random(5000, 8000)

    # 5. 添加位置标签
    if location:
        _add_location_tag(page, location)
        sleep_random(5000, 8000)

    # 6. 添加同款好物标签
    if product:
        _add_product_tag(page, product)
        sleep_random(5000, 8000)

    # 7. 关联热点
    if hotspot:
        _add_hotspot(page, hotspot)
        sleep_random(5000, 8000)

    # 8. 设置可见范围
    _set_visibility(page, visibility)

    # 9. 设置保存权限
    _set_allow_save(page, allow_save)

    logger.info("图文表单填写完成，等待用户确认发布")
    return {"success": True, "message": "表单填写完成，请确认后调用 click-publish 发布"}


def _upload_images(page: Page, image_paths: list[str]) -> None:
    """通过上传页（upload?default-tab=3）上传图片，上传完成后等待自动跳转到发布页。

    该页面有真实的常驻 file input，可直接通过 CDP DOM.setFileInputFiles 设置文件，
    无需点击触发、无需拦截动态创建的 input。
    """
    logger.info("上传 %d 张图片", len(image_paths))

    abs_paths = [str(Path(p).resolve()) for p in image_paths]

    # 导航到上传页（default-tab=3 对应图文 tab）
    page.navigate(UPLOAD_IMAGE_URL)
    page.wait_for_load()
    page.wait_dom_stable()
    sleep_random(1500, 2500)

    # 找到 file input 并直接设置文件
    file_input_selector = "input[type='file']"
    try:
        page.wait_for_element(file_input_selector, timeout=10.0)
    except Exception:
        raise PublishError("上传页未找到 file input，请确认页面已正确加载")

    page.set_file_input(file_input_selector, abs_paths)
    logger.info("已设置图片文件，等待上传完成并跳转到发布页")

    # 等待页面跳转到发布页（URL 变为 post/image）
    _wait_for_redirect_to_publish_page(page)
    logger.info("已跳转到发布页，图片上传完成")


def _wait_for_redirect_to_publish_page(page: Page, timeout: float = 60.0) -> None:
    """等待上传完成后自动跳转到图文发布页。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current_url = page.get_current_url()
        if "post/image" in current_url:
            # 等待发布页 DOM 稳定
            page.wait_dom_stable()
            sleep_random(3000, 5000)
            return
        sleep_random(800, 1500)
    raise PublishError("等待跳转到发布页超时（60秒），请检查图片是否上传成功")


def _click_upload_trigger(page: Page) -> None:
    """点击上传触发区域，唤起文件选择对话框。

    优先查找 class 前缀为 phone-container 的元素；
    若未找到，则查找子元素含「点击上传」文案且 class 前缀为 bold-text-container 的元素。
    """
    clicked = page.evaluate(
        """
        (() => {
            // 策略1：class 前缀为 phone-container 的元素
            const phoneContainers = document.querySelectorAll('[class]');
            for (const el of phoneContainers) {
                const classes = Array.from(el.classList);
                if (classes.some(c => c.startsWith('phone-container'))) {
                    const rect = el.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        el.click();
                        return 'phone-container';
                    }
                }
            }

            // 策略2：子元素含「点击上传」文案且 class 前缀为 bold-text-container 的元素
            const allEls = document.querySelectorAll('[class]');
            for (const el of allEls) {
                const classes = Array.from(el.classList);
                if (classes.some(c => c.startsWith('bold-text-container'))) {
                    // 检查自身或子元素是否含「点击上传」文案
                    if (el.textContent.includes('点击上传')) {
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            el.click();
                            return 'bold-text-container';
                        }
                    }
                }
            }

            return false;
        })()
        """
    )

    if clicked:
        logger.info("点击上传触发区域成功（策略：%s）", clicked)
        sleep_random(500, 800)
    else:
        logger.warning(
            "未找到上传触发区域（phone-container / bold-text-container），尝试直接使用 file input"
        )


def _wait_for_images_uploaded(
    page: Page, expected_count: int, timeout: float = 120.0
) -> None:
    """等待图片上传完成。"""
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        # 检查预览图数量
        preview_count = page.get_elements_count(
            "div[class*='image-card'], div[class*='preview-card'], div[class*='img-item']"
        )
        if preview_count >= expected_count:
            sleep_random(500, 1000)
            return

        # 检查是否有上传进度消失
        has_uploading = page.has_element(
            "div[class*='uploading'], div[class*='progress'], div[class*='loading']"
        )
        if not has_uploading and preview_count > 0:
            sleep_random(500, 1000)
            return

        sleep_random(800, 1500)

    # 超时但有部分上传成功
    preview_count = page.get_elements_count(
        "div[class*='image-card'], div[class*='preview-card'], div[class*='img-item']"
    )
    if preview_count > 0:
        logger.warning("部分图片上传超时: %d/%d", preview_count, expected_count)
        return

    raise UploadTimeoutError("图片上传超时，请检查网络连接")


def _fill_title(page: Page, title: str) -> None:
    """填写作品标题。

    抖音图文标题使用 semi-input（React 受控组件），需要通过 JS 模拟原生 input 事件
    才能触发 React 的 onChange，否则 value 不会被框架感知。
    """
    logger.info("填写标题: %s", title[:20])

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
        sleep_random(300, 500)
    else:
        logger.warning("未找到标题输入框，跳过")


def _fill_content(page: Page, content: str) -> None:
    """填写作品描述（含话题tag）。

    描述末尾追加一个空格，确保最后一个话题标签被正确识别。
    """
    logger.info("填写描述: %d 字", len(content))

    # 末尾追加空格，确保最后一个话题标签被平台正确识别
    content_with_trailing_space = content + " "

    content_selector = (
        "div[contenteditable='true'][placeholder*='描述'], "
        "div[contenteditable='true'][placeholder*='简介'], "
        "div[contenteditable='true'][class*='desc'], "
        "div[contenteditable='true'][class*='editor']"
    )
    if not page.has_element(content_selector):
        content_selector = "div[contenteditable='true']"

    if page.has_element(content_selector):
        page.input_content_editable(content_selector, content_with_trailing_space)
        sleep_random(300, 500)
    else:
        logger.warning("未找到描述编辑器，跳过")


def _select_music(page: Page, music_name: str) -> None:
    """选择背景音乐。

    流程：
    1. 点击「选择音乐」或「修改音乐」文字触发区域，打开音乐选择 drawer
    2. 在搜索框输入音乐名称，等待搜索结果
    3. 点击第一条结果的「使用」按钮（类名前缀 apply-btn）
    """
    logger.info("选择背景音乐: %s", music_name)

    # 点击「选择音乐」或「修改音乐」触发区域打开 drawer
    # 抖音图文页面的音乐触发区域是包含「选择音乐」文案的任意可点击元素
    music_btn_result = page.evaluate(
        """
        (() => {
            // 找所有类名前缀为 action 的元素，找出 text 为「选择音乐」的那个并点击
            const allEls = document.querySelectorAll('[class*="action"]');
            for (const el of allEls) {
                const text = el.textContent.trim();
                if (text === '选择音乐' || text === '修改音乐' || text === '添加音乐') {
                    const rect = el.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        el.click();
                        return text;
                    }
                }
            }
            return false;
        })()
        """
    )

    if not music_btn_result:
        logger.warning("未找到「选择音乐」按钮，跳过音乐选择")
        return

    logger.info("已点击音乐触发区域（%s），等待 drawer 打开", music_btn_result)
    sleep_random(3500, 6000)

    # 在搜索框中输入音乐名称
    # 搜索框：class 为 semi-input 的 input 元素且 placeholder 包含「音乐」
    search_clicked = page.evaluate(
        """
        (() => {
            const inputs = document.querySelectorAll('input.semi-input');
            for (const inp of inputs) {
                const ph = inp.getAttribute('placeholder') || '';
                if (ph.includes('音乐')) {
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

    if not search_clicked:
        logger.warning("未找到音乐搜索输入框，跳过")
        page.press_key("Escape")
        return

    logger.info("已聚焦音乐搜索框，开始输入")
    page.type_text(music_name, delay_ms=80)
    sleep_random(6000, 9500)  # 等待搜索结果加载（约3秒）

    logger.info("已输入音乐名称，等待搜索结果加载完成，准备点击「使用」按钮")

    # 在搜索结果容器（class 前缀为 music-collection-container）里
    # 找第一个 class 前缀为 card-container 的卡片，
    # 再找其内部 class 为 semi-button 且文案含「使用」的按钮并点击
    # 注意：evaluate 只能返回可序列化的值，不能返回 DOM 元素
    music_applied = page.evaluate(
        """
        (() => {
            // 找搜索结果容器（class 前缀为 music-collection-container）
            const container = document.querySelector('[class*="music-collection-container"]');
            if (!container) return 'no-container';

            // 找第一个 class 前缀为 card-container 的卡片
            const firstCard = container.querySelector('[class*="card-container"]');
            if (!firstCard) return 'no-card';

            // 鼠标悬浮触发「使用」按钮显示
            firstCard.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
            firstCard.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));

            // 找 class 为 semi-button 且文案含「使用」的按钮
            const btns = firstCard.querySelectorAll('button.semi-button');
            for (const btn of btns) {
                if (btn.textContent.trim() === '使用') {
                    btn.click();
                    return 'clicked';
                }
            }
            return 'no-use-btn';
        })()
        """
    )

    if music_applied != "clicked":
        logger.warning(
            "音乐「使用」按钮点击失败（状态: %s），关闭 drawer", music_applied
        )
        page.press_key("Escape")
        return

    sleep_random(500, 800)
    logger.info("已选择音乐: %s", music_name)


def _add_location_tag(page: Page, location: str) -> None:
    """添加位置标签。

    默认已选中「位置」标签类型，无需切换下拉。
    流程：
    1. 找到 class 为 semi-select-selection 且子元素含「输入相关位置」文案的元素
    2. 找到该元素下的 input 并聚焦
    3. 输入位置名称，等待 3 秒让搜索结果加载
    4. 在 .semi-select-option-list 容器内点击第一条 .semi-select-option
    """
    logger.info("添加位置标签: %s", location)

    # 步骤1+2：找到位置搜索框的 selection 容器，再找其内部 input 并聚焦
    input_focused = page.evaluate(
        """
        (() => {
            // 找 class 含 semi-select-selection 且子元素文案含「输入相关位置」的元素
            const selections = document.querySelectorAll('.semi-select-selection');
            for (const sel of selections) {
                if (sel.textContent.includes('输入相关位置')) {
                    // 找该元素下的 input
                    const inp = sel.querySelector('input');
                    if (inp) {
                        inp.click();
                        inp.focus();
                        return true;
                    }
                    // 兜底：点击 selection 本身触发 popover，再找 input
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
            "未找到位置搜索输入框（semi-select-selection 含「输入相关位置」），跳过"
        )
        return

    logger.info("已聚焦位置搜索输入框，开始输入")
    sleep_random(300, 500)

    # 步骤3：输入位置名称，等待搜索结果加载
    page.type_text(location, delay_ms=80)
    sleep_random(5000, 8500)  # 等待联想结果加载（约3秒）

    # 步骤4：在 .semi-select-option-list 容器内点击第一条 .semi-select-option
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
        sleep_random(500, 800)
    else:
        logger.warning("未找到位置联想结果，跳过")


def _add_product_tag(page: Page, product: str) -> None:
    """添加同款好物标签（标记万物）。

    流程：
    1. 找到 class 为 semi-select 且子元素文案含「位置」的下拉选框，点击打开 dropdown
    2. 在 dropdown 中找到 class 为 semi-select-option 且子元素文案含「标记万物」的选项并点击
    3. 找到 class 为 semi-select-selection 且子元素含「标记的物品」文案的元素，找到其内部 input 并聚焦
    4. 输入好物名称，等待 3 秒让搜索结果加载
    5. 在 .semi-select-option-list 容器内点击第一条 .semi-select-option
    """
    logger.info("添加同款好物标签: %s", product)

    # 步骤1：找到「位置」下拉选框并点击，打开 dropdown
    dropdown_clicked = page.evaluate(
        """
        (() => {
            // 找 class 含 semi-select 且子元素文案含「位置」的元素
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

    sleep_random(1500, 2800)  # 等待 dropdown 打开

    # 步骤2：在 dropdown 中选择「标记万物」选项
    option_selected = page.evaluate(
        """
        (() => {
            // 找 class 含 semi-select-option 且子元素文案含「标记万物」的选项
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

    sleep_random(500, 800)  # 等待切换完成

    # 步骤3：找到 class 为 semi-input 且 placeholder 含「标记的物品」的 input 并聚焦
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
            "未找到同款好物搜索输入框（semi-select-selection 含「标记的物品」），跳过"
        )
        return

    logger.info("已聚焦好物搜索输入框，开始输入")
    sleep_random(300, 500)

    # 步骤4：输入好物名称，等待搜索结果加载
    page.type_text(product, delay_ms=80)
    sleep_random(5000, 8500)  # 等待联想结果加载（约3秒）

    # 步骤5：找到类名前缀为 dropdown 且包含「标记同款好物」文案的容器，点击其第一个类名前缀为 option 的子元素
    option_clicked = page.evaluate(
        """
        (() => {
            // 找类名前缀为 dropdown 且子元素文案含「标记同款好物」的容器
            const dropdowns = document.querySelectorAll('[class*="dropdown"]');
            for (const dropdown of dropdowns) {
                if (dropdown.textContent.includes('标记同款好物')) {
                    // 找该容器内第一个类名前缀为 option 的子元素
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
        sleep_random(500, 800)
    else:
        logger.warning("未找到同款好物联想结果，跳过")


def _add_hotspot(page: Page, hotspot: str) -> None:
    """关联热点话题。

    流程：
    1. 找到 class 为 semi-select-selection 且子元素含「输入热点词」文案的元素
    2. 找到该元素下的 input 并聚焦
    3. 输入热点词，等待 3 秒让搜索结果加载
    4. 在 .semi-select-option-list 容器内点击第一条 .semi-select-option
    """
    logger.info("关联热点: %s", hotspot)

    # 步骤1+2：找到热点搜索框的 selection 容器，再找其内部 input 并聚焦
    input_focused = page.evaluate(
        """
        (() => {
            // 找 class 含 semi-select-selection 且子元素文案含「输入热点词」的元素
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
    sleep_random(300, 500)

    # 步骤3：输入热点词，等待搜索结果加载
    page.type_text(hotspot, delay_ms=80)
    sleep_random(5000, 7000)  # 等待联想结果加载（约3秒）

    # 步骤4：在 .semi-select-option-list 容器内点击第一条 .semi-select-option
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
        sleep_random(500, 800)
    else:
        logger.warning("未找到热点联想结果，跳过")


def _click_first_suggestion(page: Page) -> None:
    """点击搜索联想 popover 的第一条结果。"""
    clicked = page.evaluate(
        """
        (() => {
            const selectors = [
                'div[class*="popover"] li:first-child',
                'div[class*="suggest"] li:first-child',
                'div[class*="dropdown"] li:first-child',
                'ul[class*="list"] li:first-child',
                'div[class*="result"] div:first-child',
                'div[class*="item"]:first-child',
            ];
            for (const sel of selectors) {
                const el = document.querySelector(sel);
                if (el) {
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
    if clicked:
        sleep_random(300, 500)
    else:
        logger.warning("未找到搜索联想第一条结果")


def _set_visibility(page: Page, visibility: str) -> None:
    """设置可见范围（radio 单选）。"""
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
    sleep_random(200, 400)


def _set_allow_save(page: Page, allow_save: bool) -> None:
    """设置保存权限。

    实际 HTML 结构：
    <label class="radio-d4zkru">
        <input type="checkbox" class="radio-native-p6VBGt" value="1">  <!-- 允许 -->
        <span>允许 </span>
    </label>
    <label class="radio-d4zkru">
        <input type="checkbox" class="radio-native-p6VBGt" value="0">  <!-- 不允许 -->
        <span>不允许 </span>
    </label>

    通过找到包含「保存权限」文案的区域，再找对应 value 的 checkbox input 并点击。
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
                    // 向上找父容器
                    let parent = el.parentElement;
                    for (let i = 0; i < 8 && parent; i++) {{
                        // 找 value={repr(target_value)} 的 checkbox input
                        const inputs = parent.querySelectorAll('input[type="checkbox"]');
                        for (const inp of inputs) {{
                            if (inp.value === {repr(target_value)}) {{
                                // 点击其父 label
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

            // 策略2：找 class 含 radio-d4zkru 的 label，通过 span 文案匹配
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
    sleep_random(200, 300)
