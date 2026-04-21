
from __future__ import annotations

import json
import logging

from .cdp import Page, NetworkCapture
from .errors import PublishError
from .human import sleep_random
from .urls import PUBLISH_API_URL_PATTERN

logger = logging.getLogger(__name__)


def check_video_format(video_file: str, video_type: str = "normal") -> dict:
    """检测视频格式是否符合要求（大小/时长/分辨率）。

    Args:
        video_file: 视频文件路径。
        video_type: 视频类型，"normal" 为普通视频，"vr" 为全景视频。

    Returns:
        包含 valid、errors、info 的字典。
    """
    if video_type == "vr":
        from .publish_vr import check_vr_format
        return check_vr_format(video_file)
    else:
        from .publish_video import check_video_format as _check
        return _check(video_file)


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
    from .publish_video import fill_publish_video as _fill
    return _fill(
        page,
        title=title,
        content=content,
        video_file=video_file,
        topics=topics,
        location=location,
        product=product,
        hotspot=hotspot,
        visibility=visibility,
        allow_save=allow_save,
    )


def fill_publish_vr(
    page: Page,
    title: str,
    content: str,
    video_file: str,
    vr_format: str = "普通360°全景视频",
    cover: str = "",
    location: str = "",
    product: str = "",
    hotspot: str = "",
    visibility: str = "公开",
    allow_save: bool = True,
) -> dict:
    """填写全景视频发布表单（不点击发布按钮）。

    Args:
        page: CDP 页面对象。
        title: 作品标题（必填，不超过30字）。
        content: 作品简介（含话题tag，不超过800字，纯文字）。
        video_file: 全景视频文件绝对路径。
        vr_format: 全景视频格式（四种之一）。
        cover: 封面图片路径，空字符串表示使用官方生成封面。
        location: 位置标签，空字符串表示不添加。
        product: 同款好物标签，空字符串表示不添加。
        hotspot: 关联热点词，空字符串表示不关联。
        visibility: 可见范围（公开/好友可见/仅自己可见）。
        allow_save: 是否允许保存。

    Returns:
        操作结果字典，包含 success 字段。
    """
    from .publish_vr import fill_publish_vr as _fill
    return _fill(
        page,
        title=title,
        content=content,
        video_file=video_file,
        vr_format=vr_format,
        cover=cover,
        location=location,
        product=product,
        hotspot=hotspot,
        visibility=visibility,
        allow_save=allow_save,
    )


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
    from .publish_image import fill_publish_image as _fill
    return _fill(
        page,
        title=title,
        content=content,
        images=images,
        topics=topics,
        music=music,
        location=location,
        product=product,
        hotspot=hotspot,
        visibility=visibility,
        allow_save=allow_save,
    )


def fill_publish_article(
    page: Page,
    title: str,
    content: str,
    summary: str = "",
    cover: str = "",
    topics: list[str] | None = None,
    music: str = "",
    visibility: str = "公开",
) -> dict:
    """填写文章发布表单（不点击发布按钮）。

    Args:
        page: CDP 页面对象。
        title: 文章标题（必填，不超过30字）。
        content: 文章正文（必填，Markdown 格式，100~7000字）。
        summary: 文章摘要（非必填，不超过30字）。
        cover: 封面图片路径（必填）。
        topics: 话题列表（最多5个）。
        music: 背景音乐名称，空字符串表示不选择。
        visibility: 可见范围（公开/好友可见/仅自己可见）。

    Returns:
        操作结果字典，包含 success 字段。
    """
    from .publish_article import fill_publish_article as _fill
    return _fill(
        page,
        title=title,
        content=content,
        summary=summary,
        cover=cover,
        topics=topics,
        music=music,
        visibility=visibility,
    )


def click_publish(page: Page) -> dict:
    """点击发布按钮，监听抖音发布接口（create_v2）返回结果。

    应在 fill_publish_* 填写完表单、用户确认后调用。

    Args:
        page: CDP 页面对象。

    Returns:
        包含 success、item_id 等字段的结果字典。
    """
    logger.info("点击发布按钮，监听发布接口...")

    # 找到发布按钮
    publish_btn_found = page.evaluate(
        """
        (() => {
            const btns = document.querySelectorAll('button');
            for (const btn of btns) {
                const text = btn.textContent.trim();
                if (text === '发布' || text === '立即发布') {
                    if (!btn.disabled && !btn.classList.contains('disabled')) {
                        const rect = btn.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            return {x: rect.left + rect.width / 2, y: rect.top + rect.height / 2};
                        }
                    }
                }
            }
            return null;
        })()
        """
    )

    if not publish_btn_found:
        raise PublishError("未找到可点击的发布按钮，请确认表单已填写完毕")

    # 滚动到发布按钮并点击，同时监听 create_v2 接口
    with NetworkCapture(page, PUBLISH_API_URL_PATTERN, timeout=60.0) as capture:
        page.mouse_move(publish_btn_found["x"], publish_btn_found["y"])
        sleep_random(200, 400)
        page.mouse_click(publish_btn_found["x"], publish_btn_found["y"])
        logger.info("已点击发布按钮，等待发布接口响应...")

        request_data, response_data = capture.wait_for_capture()

    # 解析发布接口响应
    if response_data and response_data.get("body"):
        try:
            body = json.loads(response_data["body"])
            status_code = body.get("status_code", -1)
            if status_code == 0:
                item_id = body.get("item_id", "")
                logger.info("发布成功，item_id=%s", item_id)
                return {
                    "success": True,
                    "message": "发布成功",
                    "item_id": item_id,
                }
            else:
                error_msg = body.get("status_msg", "未知错误")
                logger.error("发布失败: status_code=%d, msg=%s", status_code, error_msg)
                return {
                    "success": False,
                    "message": f"发布失败: {error_msg}",
                    "status_code": status_code,
                }
        except json.JSONDecodeError:
            logger.warning("无法解析发布接口响应体")

    # 降级：通过页面状态判断
    logger.warning("未捕获到发布接口响应，通过页面状态判断")
    sleep_random(3000, 5000)

    current_url = page.get_current_url()
    if "manage" in current_url or "work" in current_url:
        return {"success": True, "message": "发布成功（页面跳转确认）"}

    success_text = page.evaluate(
        """
        (() => {
            const el = document.querySelector(
                'div[class*="success"], div[class*="toast"], span[class*="success"]'
            );
            return el ? el.textContent.trim() : '';
        })()
        """
    )
    if success_text and ("成功" in success_text or "发布" in success_text):
        return {"success": True, "message": f"发布成功: {success_text}"}

    error_text = page.evaluate(
        """
        (() => {
            const el = document.querySelector(
                'div[class*="error"], div[class*="warning"], span[class*="error"]'
            );
            return el ? el.textContent.trim() : '';
        })()
        """
    )
    if error_text:
        return {"success": False, "message": f"发布失败: {error_text}"}

    return {"success": False, "message": "发布状态未知，请手动确认"}