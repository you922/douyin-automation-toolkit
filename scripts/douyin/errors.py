"""自定义异常类。"""

from __future__ import annotations


class CDPError(Exception):
    """CDP 通信错误。"""


class ElementNotFoundError(Exception):
    """DOM 元素未找到。"""

    def __init__(self, selector: str) -> None:
        self.selector = selector
        super().__init__(f"元素未找到: {selector}")


class CaptchaDetectedError(Exception):
    """检测到验证码。"""


class RateLimitError(Exception):
    """触发频率限制。"""


class LoginRequiredError(Exception):
    """未登录或登录态失效。"""


class PublishError(Exception):
    """发布失败。"""


class UploadTimeoutError(Exception):
    """上传超时。"""


class TitleTooLongError(Exception):
    """标题超长。"""

    def __init__(self, current: str, limit: str) -> None:
        self.current = current
        self.limit = limit
        super().__init__(f"标题超长: {current}/{limit}")


class ContentTooLongError(Exception):
    """正文超长。"""

    def __init__(self, current: str, limit: str) -> None:
        self.current = current
        self.limit = limit
        super().__init__(f"正文超长: {current}/{limit}")


class NoFeedsError(Exception):
    """没有搜索结果。"""

class NoVideosError(Exception):
    """没有捕获到 videos 数据。"""

    def __init__(self) -> None:
        super().__init__("没有捕获到 videos 数据")

class NoFeedDetailError(Exception):
    """无法获取内容详情。"""

class NoVideoDetailError(Exception):
    """没有捕获到视频详情数据。"""

    def __init__(self) -> None:
        super().__init__("没有捕获到视频详情数据")

class NotLoggedInError(Exception):
    """未登录。"""

    def __init__(self) -> None:
        super().__init__("未登录，请先扫码登录")

class PageNotAccessibleError(Exception):
    """页面不可访问。"""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"视频不可访问: {reason}")


class HotListFetchError(Exception):
    """热榜获取失败。"""


class HotListEmptyError(Exception):
    """热榜数据为空。"""

    def __init__(self) -> None:
        super().__init__("热榜数据为空，未获取到任何条目")
