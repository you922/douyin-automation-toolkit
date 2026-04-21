"""URL 模板集中管理。"""

from __future__ import annotations

from urllib.parse import quote

# 首页
HOME_URL = "https://www.douyin.com"

# 发现页
EXPLORE_URL = "https://www.douyin.com/discover"

# 发布页（创作者中心，旧版上传入口）
PUBLISH_URL = "https://creator.douyin.com/creator-micro/content/upload"

# 图文上传入口页（有真实 file input，上传完自动跳转到发布页）
UPLOAD_IMAGE_URL = (
    "https://creator.douyin.com/creator-micro/content/upload?default-tab=3"
)

# 普通视频发布页
PUBLISH_VIDEO_URL = (
    "https://creator.douyin.com/creator-micro/content/post/video"
    "?default-tab=5&enter_from=publish_page&media_type=video&type=new"
)

# 全景视频发布页
PUBLISH_VR_URL = (
    "https://creator.douyin.com/creator-micro/content/post/vr"
    "?default-tab=4&enter_from=publish_page"
)

# 图文发布页
PUBLISH_IMAGE_URL = (
    "https://creator.douyin.com/creator-micro/content/post/image"
    "?enter_from=publish_page&media_type=image&type=new"
)

# 文章发布页
PUBLISH_ARTICLE_URL = (
    "https://creator.douyin.com/creator-micro/content/post/article"
    "?default-tab=5&enter_from=publish_page&media_type=article&type=new"
)

# 发布接口（监听用）
PUBLISH_API_URL_PATTERN = "aweme/create_v2"


# 热榜 API（在浏览器上下文中使用相对路径）
HOT_SEARCH_LIST_API = "/aweme/v1/hot/search/list/"

# 热点频道 API（返回热榜关联视频，含完整 aweme_list）
HOT_CHANNEL_HOTSPOT_API = "/aweme/v1/web/channel/hotspot"

# 热榜页面（降级用）
HOT_LIST_PAGE_URL = "https://www.douyin.com/hot"


def make_search_url(keyword: str) -> str:
    """生成搜索页 URL。"""
    return f"https://www.douyin.com/search/{quote(keyword)}?type=video"


def make_video_detail_url(video_id: str) -> str:
    """生成视频详情页 URL。"""
    return f"https://www.douyin.com/video/{video_id}"

def make_note_detail_url(note_id: str) -> str:
    """生成笔记（图文）详情页 URL。"""
    return f"https://www.douyin.com/note/{note_id}"

def make_aweme_detail_url(aweme_id: str, aweme_type: int = 0) -> str:
    """根据类型生成详情页 URL（视频或笔记）。

    Args:
        aweme_id: 作品 ID。
        aweme_type: 0=视频, 68=笔记/图文。
    """
    if aweme_type == 68:
        return make_note_detail_url(aweme_id)
    return make_video_detail_url(aweme_id)


def make_user_profile_url(user_id: str) -> str:
    """生成用户主页 URL。

    抖音用户主页支持 sec_uid 和短链两种格式。
    user_id 传 "self" 表示当前登录用户（我的主页）。
    """
    return f"https://www.douyin.com/user/{user_id}"
