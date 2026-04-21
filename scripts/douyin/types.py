"""数据类型定义。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote


@dataclass
class VideoInfo:
    """视频基本信息。"""

    video_id: str = ""
    title: str = ""
    author: str = ""
    author_id: str = ""
    like_count: int = 0
    comment_count: int = 0
    share_count: int = 0
    collect_count: int = 0
    url: str = ""
    cover_url: str = ""
    create_time: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VideoInfo:
        """从字典创建。"""
        return cls(
            video_id=data.get("video_id", "") or data.get("aweme_id", ""),
            title=data.get("title", "") or data.get("desc", ""),
            author=data.get("author", ""),
            author_id=data.get("author_id", ""),
            like_count=data.get("like_count", 0),
            comment_count=data.get("comment_count", 0),
            share_count=data.get("share_count", 0),
            collect_count=data.get("collect_count", 0),
            url=data.get("url", ""),
            cover_url=data.get("cover_url", ""),
            create_time=data.get("create_time", 0),
        )

    def to_dict(self) -> dict[str, Any]:
        """转为字典。"""
        return {
            "video_id": self.video_id,
            "title": self.title,
            "author": self.author,
            "author_id": self.author_id,
            "like_count": self.like_count,
            "comment_count": self.comment_count,
            "share_count": self.share_count,
            "collect_count": self.collect_count,
            "url": self.url,
            "cover_url": self.cover_url,
            "create_time": self.create_time,
        }


@dataclass
class Feed:
    """搜索结果项（与 VideoInfo 类似，但包含搜索上下文）。"""

    video_id: str = ""
    title: str = ""
    author: str = ""
    author_id: str = ""
    author_sec_uid: str = ""
    author_signature: str = ""
    like_count: int = 0
    comment_count: int = 0
    share_count: int = 0
    collect_count: int = 0
    url: str = ""
    cover_url: str = ""
    play_url: str = ""
    duration: int = 0
    create_time: int = 0
    xsec_token: str = ""
    aweme_type: int = 0  # 0=视频, 68=笔记/图文
    images: list[str] = field(default_factory=list)  # 笔记图片 URL 列表

    @property
    def is_note(self) -> bool:
        """是否为笔记（图文）类型。"""
        return self.aweme_type == 68

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Feed:
        """从字典创建（兼容 SSR 和 API 两种数据格式）。"""
        # 兼容 aweme_info 嵌套结构
        aweme_info = data.get("aweme_info", data)
        if not isinstance(aweme_info, dict):
            aweme_info = data

        author_info = aweme_info.get("author", {})
        statistics = aweme_info.get("statistics", {})
        video_obj = aweme_info.get("video") or {}
        video_id = aweme_info.get("aweme_id", "") or data.get("video_id", "")

        # aweme_type: 0=视频, 68=笔记/图文
        aweme_type = aweme_info.get("aweme_type", 0) or data.get("aweme_type", 0)

        # cover_url: 优先 video.cover.url_list[0]（API 格式），其次顶层 cover_url（SSR 格式）
        cover_url = ""
        cover_data = video_obj.get("cover") if isinstance(video_obj, dict) else {}
        if isinstance(cover_data, dict):
            url_list = cover_data.get("url_list") or []
            if url_list:
                cover_url = url_list[0]
        if not cover_url:
            cover_url = data.get("cover_url", "") or ""

        author_sec_uid = (
            author_info.get("sec_uid", "") if isinstance(author_info, dict) else ""
        )
        author_signature = (
            author_info.get("signature", "") if isinstance(author_info, dict) else ""
        )
        duration = video_obj.get("duration", 0) if isinstance(video_obj, dict) else 0

        # play_url: API 的 video.play_addr.url_list[0]，fallback 到 download_addr
        play_url = ""
        if isinstance(video_obj, dict):
            pa = video_obj.get("play_addr") or {}
            pa_urls = pa.get("url_list") or []
            if pa_urls:
                play_url = pa_urls[0]
            if not play_url:
                da = video_obj.get("download_addr") or {}
                da_urls = da.get("url_list") or []
                if da_urls:
                    play_url = da_urls[0]
        if not play_url:
            play_url = data.get("play_url", "") or ""

        # 笔记图片列表：从 image_post_info.images 提取
        images: list[str] = []
        if aweme_type == 68:
            image_post_info = aweme_info.get("image_post_info") or {}
            raw_images = image_post_info.get("images") or []
            for img in raw_images:
                if not isinstance(img, dict):
                    continue
                display_image = img.get("display_image") or {}
                img_url_list = display_image.get("url_list") or []
                if img_url_list:
                    images.append(img_url_list[0])
        if not images:
            images = data.get("images") or []

        # URL: 笔记用 /note/，视频用 /video/
        if video_id:
            url_path = "note" if aweme_type == 68 else "video"
            url = f"https://www.douyin.com/{url_path}/{video_id}"
        else:
            url = ""

        return cls(
            video_id=video_id,
            title=aweme_info.get("desc", "") or data.get("title", ""),
            author=author_info.get("nickname", "") if isinstance(author_info, dict) else "",
            author_id=(
                author_info.get("unique_id", "") or author_info.get("short_id", "")
                if isinstance(author_info, dict)
                else ""
            ),
            author_sec_uid=author_sec_uid,
            author_signature=author_signature,
            like_count=statistics.get("digg_count", 0) if isinstance(statistics, dict) else 0,
            comment_count=(
                statistics.get("comment_count", 0) if isinstance(statistics, dict) else 0
            ),
            share_count=(
                statistics.get("share_count", 0) if isinstance(statistics, dict) else 0
            ),
            collect_count=(
                statistics.get("collect_count", 0) if isinstance(statistics, dict) else 0
            ),
            url=url,
            cover_url=cover_url,
            play_url=play_url,
            duration=duration,
            create_time=aweme_info.get("create_time", 0) or data.get("create_time", 0),
            xsec_token=data.get("xsec_token", ""),
            aweme_type=aweme_type,
            images=images,
        )

    def to_dict(self) -> dict[str, Any]:
        """转为字典。"""
        result = {
            "video_id": self.video_id,
            "title": self.title,
            "author": self.author,
            "author_id": self.author_id,
            "author_sec_uid": self.author_sec_uid,
            "author_signature": self.author_signature,
            "like_count": self.like_count,
            "comment_count": self.comment_count,
            "share_count": self.share_count,
            "collect_count": self.collect_count,
            "url": self.url,
            "cover_url": self.cover_url,
            "play_url": self.play_url,
            "duration": self.duration,
            "create_time": self.create_time,
            "aweme_type": self.aweme_type,
        }
        if self.is_note and self.images:
            result["images"] = self.images
        return result


@dataclass
class Comment:
    """评论数据。"""

    comment_id: str = ""
    author: str = ""
    content: str = ""
    like_count: int = 0
    reply_count: int = 0
    create_time: int = 0

    def to_dict(self) -> dict[str, Any]:
        """转为字典。"""
        return {
            "comment_id": self.comment_id,
            "author": self.author,
            "content": self.content,
            "like_count": self.like_count,
            "reply_count": self.reply_count,
            "create_time": self.create_time,
        }


@dataclass
class UserBasicInfo:
    """用户基本信息。"""

    user_id: str = ""
    sec_uid: str = ""
    nickname: str = ""
    signature: str = ""
    avatar_url: str = ""
    follower_count: int = 0
    following_count: int = 0
    total_favorited: int = 0
    aweme_count: int = 0
    ip_location: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserBasicInfo:
        """从字典创建。"""
        return cls(
            user_id=data.get("uid", "") or data.get("user_id", ""),
            sec_uid=data.get("sec_uid", ""),
            nickname=data.get("nickname", ""),
            signature=data.get("signature", ""),
            avatar_url=data.get("avatar_larger", {}).get("url_list", [""])[0]
            if isinstance(data.get("avatar_larger"), dict)
            else data.get("avatar_url", ""),
            follower_count=data.get("follower_count", 0),
            following_count=data.get("following_count", 0),
            total_favorited=data.get("total_favorited", 0),
            aweme_count=data.get("aweme_count", 0),
            ip_location=data.get("ip_location", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        """转为字典。"""
        return {
            "user_id": self.user_id,
            "sec_uid": self.sec_uid,
            "nickname": self.nickname,
            "signature": self.signature,
            "avatar_url": self.avatar_url,
            "follower_count": self.follower_count,
            "following_count": self.following_count,
            "total_favorited": self.total_favorited,
            "aweme_count": self.aweme_count,
            "ip_location": self.ip_location,
        }


@dataclass
class UserProfileResponse:
    """用户主页响应。"""

    user_basic_info: UserBasicInfo = field(default_factory=UserBasicInfo)
    feeds: list[Feed] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """转为字典。"""
        return {
            "user_basic_info": self.user_basic_info.to_dict(),
            "feeds": [f.to_dict() for f in self.feeds],
        }


@dataclass
class FilterOption:
    """搜索筛选选项。"""

    label: str = ""  # 选项文本（如 "综合"、"最多点赞"、"一周内"）
    active: bool = False  # 是否当前选中

    def to_dict(self) -> dict[str, Any]:
        """转为字典。"""
        return {
            "label": self.label,
            "active": self.active,
        }


@dataclass
class ActionResult:
    """互动操作结果。"""

    video_id: str = ""
    success: bool = False
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转为字典。"""
        return {
            "video_id": self.video_id,
            "success": self.success,
            "message": self.message,
        }


@dataclass
class PublishImageContent:
    """图文发布内容。"""

    title: str = ""
    content: str = ""
    image_paths: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    schedule_time: str | None = None
    is_original: bool = False
    visibility: str = "公开"
    # 图文专属
    music: str = ""          # 背景音乐名称，空字符串表示不选
    location: str = ""       # 位置标签，空字符串表示不添加
    product: str = ""        # 同款好物标签，空字符串表示不添加
    hotspot: str = ""        # 关联热点词，空字符串表示不关联
    allow_save: bool = True  # 是否允许保存

    def to_dict(self) -> dict[str, Any]:
        """转为字典。"""
        return {
            "title": self.title,
            "content": self.content,
            "image_paths": self.image_paths,
            "tags": self.tags,
            "schedule_time": self.schedule_time,
            "is_original": self.is_original,
            "visibility": self.visibility,
            "music": self.music,
            "location": self.location,
            "product": self.product,
            "hotspot": self.hotspot,
            "allow_save": self.allow_save,
        }

@dataclass
class PublishVideoContent:
    """普通视频发布内容。"""

    title: str = ""
    content: str = ""
    tags: list[str] = field(default_factory=list)
    video_path: str = ""
    schedule_time: str | None = None
    visibility: str = "公开"
    # 普通视频专属
    cover_path: str = ""     # 封面图片路径，空字符串表示使用官方生成
    location: str = ""       # 位置标签
    product: str = ""        # 同款好物标签
    hotspot: str = ""        # 关联热点词
    allow_save: bool = True  # 是否允许保存

    def to_dict(self) -> dict[str, Any]:
        """转为字典。"""
        return {
            "title": self.title,
            "content": self.content,
            "tags": self.tags,
            "video_path": self.video_path,
            "schedule_time": self.schedule_time,
            "visibility": self.visibility,
            "cover_path": self.cover_path,
            "location": self.location,
            "product": self.product,
            "hotspot": self.hotspot,
            "allow_save": self.allow_save,
        }

@dataclass
class PublishVRContent:
    """全景视频发布内容。"""

    title: str = ""
    content: str = ""
    tags: list[str] = field(default_factory=list)
    video_path: str = ""
    vr_format: str = "普通360°全景视频"  # 四种格式之一
    visibility: str = "公开"
    cover_path: str = ""  # 封面图片路径，空字符串表示使用官方生成
    location: str = ""    # 位置标签

    def to_dict(self) -> dict[str, Any]:
        """转为字典。"""
        return {
            "title": self.title,
            "content": self.content,
            "tags": self.tags,
            "video_path": self.video_path,
            "vr_format": self.vr_format,
            "visibility": self.visibility,
            "cover_path": self.cover_path,
            "location": self.location,
        }

@dataclass
class PublishArticleContent:
    """文章发布内容。"""

    title: str = ""
    body: str = ""           # 文章正文（Markdown 格式）
    summary: str = ""        # 文章摘要，不超过30字
    cover_path: str = ""     # 封面图片路径（必填）
    topics: list[str] = field(default_factory=list)  # 话题列表，最多5个
    music: str = ""          # 背景音乐名称，空字符串表示不选
    visibility: str = "公开"

    def to_dict(self) -> dict[str, Any]:
        """转为字典。"""
        return {
            "title": self.title,
            "body": self.body,
            "summary": self.summary,
            "cover_path": self.cover_path,
            "topics": self.topics,
            "music": self.music,
            "visibility": self.visibility,
        }

# ========== 热榜话题 ==========

@dataclass
class HotTopic:
    """抖音热榜话题。

    每条热榜数据本质上是一个热搜话题（word），包含话题元数据和关联视频信息。
    热搜词可作为 keyword 与 videos 表通过 keywords 字段关联。
    """

    rank: int = 0
    word: str = ""
    hot_value: int = 0
    position: int = 0
    cover_url: str = ""
    word_type: int = 0
    group_id: str = ""
    sentence_id: str = ""
    sentence_tag: int = 0
    event_time: int = 0
    video_count: int = 0
    discuss_video_count: int = 0
    label: int = 0
    related_words: str = "[]"
    search_url: str = ""
    fetch_time: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any], index: int = 0) -> HotTopic:
        """从抖音 API word_list 条目创建。

        Args:
            data: 单条 word_list 元素。
            index: 在列表中的索引（用于生成 rank）。
        """
        word = data.get("word", "") or ""

        cover_url = ""
        word_cover = data.get("word_cover")
        if isinstance(word_cover, dict):
            url_list = word_cover.get("url_list") or []
            if url_list:
                cover_url = url_list[0]

        related = data.get("related_words")
        related_json = json.dumps(related, ensure_ascii=False) if related else "[]"

        return cls(
            rank=index + 1,
            word=word or "无标题",
            hot_value=data.get("hot_value", 0) or 0,
            position=data.get("position", 0) or 0,
            cover_url=cover_url,
            word_type=data.get("word_type", 0) or 0,
            group_id=str(data.get("group_id", "") or ""),
            sentence_id=str(data.get("sentence_id", "") or ""),
            sentence_tag=data.get("sentence_tag", 0) or 0,
            event_time=data.get("event_time", 0) or 0,
            video_count=data.get("video_count", 0) or 0,
            discuss_video_count=data.get("discuss_video_count", 0) or 0,
            label=data.get("label", 0) or 0,
            related_words=related_json,
            search_url=f"https://www.douyin.com/search/{quote(word)}",
            fetch_time=datetime.now(timezone.utc).isoformat(),
        )

    def to_dict(self) -> dict[str, Any]:
        """转为字典。"""
        return {
            "rank": self.rank,
            "word": self.word,
            "hot_value": self.hot_value,
            "position": self.position,
            "cover_url": self.cover_url,
            "word_type": self.word_type,
            "group_id": self.group_id,
            "sentence_id": self.sentence_id,
            "sentence_tag": self.sentence_tag,
            "event_time": self.event_time,
            "video_count": self.video_count,
            "discuss_video_count": self.discuss_video_count,
            "label": self.label,
            "related_words": self.related_words,
            "search_url": self.search_url,
            "fetch_time": self.fetch_time,
        }

    @staticmethod
    def parse_aweme_infos(data: dict[str, Any]) -> list[dict[str, Any]]:
        """提取 word_list 条目中的 aweme_infos 原始数据。

        返回 aweme_info 字典列表，可用 Feed.from_dict 解析。
        """
        aweme_infos = data.get("aweme_infos")
        if not aweme_infos or not isinstance(aweme_infos, list):
            return []
        return [{"aweme_info": info} for info in aweme_infos if isinstance(info, dict)]

# ========== 视频播放元数据（分辨率、时长、播放地址） ==========

@dataclass
class VideoPlayInfo:
    """视频播放信息。"""

    play_url: str = ""
    duration: int = 0
    width: int = 0
    height: int = 0

    @classmethod
    def from_dict(cls, data: dict) -> VideoPlayInfo:
        return cls(
            play_url=data.get("url", "") or data.get("play_addr", {}).get("url_list", [""])[0],
            duration=data.get("duration", 0),
            width=data.get("width", 0),
            height=data.get("height", 0),
        )

@dataclass
class Cover:
    """封面信息。"""

    url: str = ""
    width: int = 0
    height: int = 0

    @classmethod
    def from_dict(cls, data: dict) -> Cover:
        url_list = data.get("url_list", [])
        return cls(
            url=url_list[0] if url_list else data.get("url", ""),
            width=data.get("width", 0),
            height=data.get("height", 0),
        )

@dataclass
class User:
    """用户信息（嵌套在视频卡片中）。"""

    user_id: str = ""
    nickname: str = ""
    avatar: str = ""
    signature: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> User:
        return cls(
            user_id=data.get("uid", "") or data.get("user_id", "") or data.get("sec_uid", ""),
            nickname=data.get("nickname", "") or data.get("name", ""),
            avatar=(
                (data.get("avatar_thumb", {}).get("url_list", [""])[0])
                if isinstance(data.get("avatar_thumb"), dict)
                else data.get("avatar", "")
            ),
            signature=data.get("signature", ""),
        )

@dataclass
class InteractInfo:
    """互动统计信息。"""

    liked: bool = False
    liked_count: int = 0
    comment_count: int = 0
    collected_count: int = 0
    shared_count: int = 0

    @classmethod
    def from_dict(cls, data: dict) -> InteractInfo:
        return cls(
            liked=data.get("is_digg", False) or data.get("liked", False),
            liked_count=data.get("digg_count", 0) or data.get("liked_count", 0),
            comment_count=data.get("comment_count", 0),
            collected_count=data.get("collect_count", 0),
            shared_count=data.get("share_count", 0),
        )

@dataclass
class VideoCard:
    """视频卡片（组合了用户、互动、封面、播放信息）。"""

    video_id: str = ""
    title: str = ""
    desc: str = ""
    user: User = field(default_factory=User)
    interact_info: InteractInfo = field(default_factory=InteractInfo)
    cover: Cover = field(default_factory=Cover)
    play_info: VideoPlayInfo | None = None
    create_time: int = 0

    @classmethod
    def from_dict(cls, data: dict) -> VideoCard:
        video_data = data.get("video", {})
        return cls(
            video_id=data.get("aweme_id", "") or data.get("video_id", ""),
            title=data.get("title", ""),
            desc=data.get("desc", ""),
            user=User.from_dict(data.get("author", {}) or data.get("user", {})),
            interact_info=InteractInfo.from_dict(
                data.get("statistics", {}) or data.get("interact_info", {})
            ),
            cover=Cover.from_dict(video_data.get("cover", {}) or data.get("cover", {})),
            play_info=VideoPlayInfo.from_dict(video_data) if video_data else None,
            create_time=data.get("create_time", 0),
        )

    def to_dict(self) -> dict:
        return {
            "videoId": self.video_id,
            "title": self.title,
            "desc": self.desc,
            "user": {
                "userId": self.user.user_id,
                "nickname": self.user.nickname,
            },
            "interactInfo": {
                "likedCount": self.interact_info.liked_count,
                "commentCount": self.interact_info.comment_count,
                "collectedCount": self.interact_info.collected_count,
                "sharedCount": self.interact_info.shared_count,
            },
            "cover": self.cover.url,
        }

# ========== 视频详情响应 ==========

@dataclass
class CommentDetail:
    """评论详情（比 Comment 更丰富，含子评论和 IP 归属地）。"""

    id: str = ""
    video_id: str = ""
    content: str = ""
    like_count: int = 0
    create_time: int = 0
    ip_location: str = ""
    liked: bool = False
    user_info: User = field(default_factory=User)
    sub_comment_count: int = 0
    sub_comments: list[CommentDetail] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> CommentDetail:
        return cls(
            id=data.get("cid", "") or data.get("comment_id", ""),
            video_id=data.get("aweme_id", "") or data.get("video_id", ""),
            content=data.get("text", ""),
            like_count=data.get("digg_count", 0) or data.get("like_count", 0),
            create_time=data.get("create_time", 0),
            ip_location=data.get("ip_label", "") or data.get("ip_location", ""),
            liked=data.get("is_digg", False) or data.get("liked", False),
            user_info=User.from_dict(data.get("user", {})),
            sub_comment_count=data.get("reply_comment_total", 0) or data.get("sub_comment_count", 0),
            sub_comments=[cls.from_dict(c) for c in data.get("reply_comment", []) or []],
        )

    def to_dict(self) -> dict:
        result: dict = {
            "id": self.id,
            "content": self.content,
            "likeCount": self.like_count,
            "createTime": self.create_time,
            "ipLocation": self.ip_location,
            "user": {
                "userId": self.user_info.user_id,
                "nickname": self.user_info.nickname,
            },
            "subCommentCount": self.sub_comment_count,
        }
        if self.sub_comments:
            result["subComments"] = [c.to_dict() for c in self.sub_comments]
        return result

@dataclass
class CommentList:
    """评论列表（含分页信息）。"""

    list_: list[CommentDetail] = field(default_factory=list)
    cursor: str = ""
    has_more: bool = False

    @classmethod
    def from_dict(cls, data: dict | list) -> CommentList:
        if isinstance(data, list):
            raw_list = data
            cursor, has_more = "", False
        else:
            raw_list = data.get("comments", []) or data.get("list", []) or []
            cursor = data.get("cursor", "") or str(data.get("has_more", ""))
            has_more = data.get("has_more", False)
        return cls(
            list_=[CommentDetail.from_dict(c) for c in raw_list],
            cursor=cursor,
            has_more=has_more,
        )

@dataclass
class VideoDetail:
    """视频详情。"""

    video_id: str = ""
    title: str = ""
    desc: str = ""
    create_time: int = 0
    user: User = field(default_factory=User)
    interact_info: InteractInfo = field(default_factory=InteractInfo)
    play_info: VideoPlayInfo | None = None
    cover: Cover = field(default_factory=Cover)

    @classmethod
    def from_dict(cls, data: dict) -> VideoDetail:
        video_data = data.get("video", {})
        return cls(
            video_id=data.get("aweme_id", "") or data.get("video_id", ""),
            title=data.get("title", ""),
            desc=data.get("desc", ""),
            create_time=data.get("create_time", 0),
            user=User.from_dict(data.get("author", {}) or data.get("user", {})),
            interact_info=InteractInfo.from_dict(
                data.get("statistics", {}) or data.get("interact_info", {})
            ),
            play_info=VideoPlayInfo.from_dict(video_data) if video_data else None,
            cover=Cover.from_dict(video_data.get("cover", {}) or data.get("cover", {})),
        )

    def to_dict(self) -> dict:
        return {
            "videoId": self.video_id,
            "title": self.title,
            "desc": self.desc,
            "createTime": self.create_time,
            "user": {
                "userId": self.user.user_id,
                "nickname": self.user.nickname,
            },
            "interactInfo": {
                "liked": self.interact_info.liked,
                "likedCount": self.interact_info.liked_count,
                "commentCount": self.interact_info.comment_count,
                "collectedCount": self.interact_info.collected_count,
                "sharedCount": self.interact_info.shared_count,
            },
        }

@dataclass
class VideoDetailResponse:
    """视频详情响应（含评论列表）。"""

    video: VideoDetail = field(default_factory=VideoDetail)
    comments: CommentList = field(default_factory=CommentList)

    @classmethod
    def from_dict(cls, data: dict) -> VideoDetailResponse:
        return cls(
            video=VideoDetail.from_dict(data.get("aweme_detail", {}) or data.get("video", {})),
            comments=CommentList.from_dict(data.get("comments", {})),
        )

    def to_dict(self) -> dict:
        return {
            "video": self.video.to_dict(),
            "comments": [c.to_dict() for c in self.comments.list_],
        }
