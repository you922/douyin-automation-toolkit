"""CSS 选择器集中管理。

抖音改版时只需修改此文件。

选择器策略优先级：
1. 属性选择器（href、type、placeholder、aria-* 等）
2. 文本内容匹配（XPath）
3. 语义化标签和属性
4. 元素层级关系
"""

# header 菜单区域，限制查找范围
HEADER_MENU_CONTAINER = "#douyin-header-menuCt"

# data 属性（最稳定）
LIVE_AVATAR = '[data-e2e="live-avatar"]'
# 头像容器 class（语义化）
AVATAR_CONTAINER = "span.avatar-component-avatar-container"
# 用户主页链接
USER_SELF_LINK = 'a[href*="/user/self"]'

# ========== 登录相关 ==========

# 登录按钮（header 内，通过文字匹配）
LOGIN_BUTTON_XPATH = "//*[@id='douyin-header-menuCt']//button[contains(., '登录')]"

# 登录弹窗 ID 关键词
LOGIN_POPUP_ID_KEYWORDS = [
    "login-panel-new",
    "douyin-login-new-id",
    "login",
    "douyin-login",
]

# 一键登录按钮（与验证码提交按钮共用 id，通过文本「一键登录」区分）
ONE_CLICK_LOGIN_BUTTON_XPATH = (
    "//*[@id='douyin_login_comp_btn_id' and normalize-space(.)='一键登录']"
)

# 登录其他账号按钮（登录弹窗内 p 标签，需点击父元素）
LOGIN_OTHER_ACCOUNT_XPATH = (
    "//*[contains(@id, 'login')]//p[contains(., '登录其他账号')]"
)

# ========== 二维码 ==========

# 二维码容器
QRCODE_CONTAINER = "#animate_qrcode_container"
# 二维码图片（aria-label 属性）
QRCODE_IMG = 'img[aria-label="二维码"]'

# ========== 退出登录 ==========

# 退出登录按钮（header 菜单内 p 标签，需点击父元素）
LOGOUT_BUTTON_XPATH = (
    "//*[@id='douyin-header-menuCt']//p[contains(., '退出登录')]"
)

# 保存登录信息开关（header 菜单内，hover 头像后显示）
# uncheck 时需点击以开启，check 时已开启无需操作
TRUST_LOGIN_SWITCH_UNCHECK_XPATH = (
    "//*[@id='douyin-header-menuCt']//*[contains(@class, 'trust-login-switch-button') and contains(@class, 'uncheck')]"
)

# ========== 手机号登录 ==========
# 手机号输入框（id 来自 douyin_login_comp_normal_input_id 容器内）
PHONE_INPUT = "#normal-input"
PHONE_INPUT_FALLBACK = "input[name='normal-input'], input[placeholder*='手机号']"
# 通过容器 id 查找内部手机号 input（排除国家码 input）
PHONE_INPUT_VIA_CONTAINER = (
    "#douyin_login_comp_normal_input_id input[name='normal-input'], "
    "#douyin_login_comp_normal_input_id input[type='tel']"
)
# 验证码输入框（id 来自 douyin_login_comp_button_input_id 容器内）
CODE_INPUT = "#button-input"
CODE_INPUT_FALLBACK = "input[name='button-input'], input[placeholder*='验证码']"
CODE_INPUT_VIA_CONTAINER = (
    "#douyin_login_comp_button_input_id input[name='button-input'], "
    "#douyin_login_comp_button_input_id input[type='tel']"
)
# 验证码登录 Tab（登录弹窗内）
PHONE_LOGIN_TAB_XPATH = (
    "//*[contains(@id, 'login')]//*[contains(., '验证码登录')]"
)
# 获取验证码按钮（登录弹窗内）
GET_CODE_BUTTON_XPATH = (
    "//*[contains(@id, 'login')]//*[contains(., '获取验证码')]"
)
# 验证码提交登录按钮（与一键登录共用 id，通过文本「登录」或「确认登录」区分）
CODE_SUBMIT_BUTTON_XPATH = (
    "//*[@id='douyin_login_comp_btn_id' and (normalize-space(.)='登录' or normalize-space(.)='确认登录')]"
)

# ========== 身份验证弹窗（扫码后可能出现） ==========
# 身份验证弹窗容器
SECOND_VERIFY_CONTAINER = "#uc-second-verify"
# 「接收短信验证码」选项（id=uc-second-verify 下的 div.uc_verification_component_list_item，包含「接收短信验证码」文字）
RECEIVE_SMS_OPTION_XPATH = (
    "//*[@id='uc-second-verify']//div[contains(@class, 'uc_verification_component_list_item') and contains(., '接收短信验证码')]"
)
# 身份验证弹窗内的验证码输入框（id=button-input, placeholder=请输入验证码）
SECOND_VERIFY_CODE_INPUT = (
    "#uc-second-verify input[name='button-input'], "
    "#uc-second-verify input[placeholder*='验证码']"
)
# 身份验证弹窗的「验证」提交按钮（通过文字精确匹配，排除「接收短信验证码」等）
SECOND_VERIFY_SUBMIT_BUTTON_XPATH = (
    "//*[@id='uc-second-verify']//*[normalize-space(.)='验证']"
)

# ========== 搜索页 ==========
# 搜索输入框（参考其他 skill：data-e2e 最稳定，placeholder/type 兜底）
SEARCH_INPUT = (
    "input[data-e2e='searchbar-input'], "
    "input[placeholder*='搜索'], "
    "input[type='text'][maxlength='100'], "
    "input[class*='search-input']"
)
# 搜索提交按钮
SEARCH_SUBMIT = (
    "button[data-e2e='searchbar-button'], "
    "button[class*='search-btn']"
)
# 搜索结果容器（参考：waterFallScrollContainer 为瀑布流容器）
SEARCH_RESULTS_CONTAINER = (
    "#waterFallScrollContainer, "
    "ul[data-e2e='scroll-list'], "
    "div[data-e2e='scroll-child']"
)
# 搜索工具栏（筛选按钮所在容器）
SEARCH_TOOLBAR_CONTAINER = "#search-toolbar-container"
# 搜索结果视频链接
SEARCH_VIDEO_LINK = "a[href*='/video/']"
# 搜索结果项容器
SEARCH_RESULT_ITEM = (
    "ul[data-e2e='scroll-list'] > li, "
    "div[data-e2e='scroll-child'] > ul > li, "
    "div[class*='video-list-item']"
)
# 搜索 Tab - 视频
SEARCH_TAB_VIDEO = (
    "a[href*='type=video'], "
    "div[data-e2e='search-tab'] a:first-child, "
    "span[class*='tab'][class*='video'], "
    "li[class*='tab'] a[href*='type=video']"
)
# 搜索筛选按钮
SEARCH_FILTER_BUTTON = (
    "div[data-e2e='search-filter'], "
    "div[class*='filter-btn'], "
    "span[class*='filter']"
)
# 搜索筛选选项
SEARCH_FILTER_OPTION = (
    "div[class*='filter-item'], "
    "li[class*='filter-option'], "
    "span[class*='filter-text']"
)
# 筛选面板
FILTER_PANEL = "div[class*='filter-panel'], div[class*='filterPanel']"

# ========== 视频详情页 ==========
# 视频详情容器（含 data-e2e-aweme-id 为视频 ID）
DETAIL_VIDEO_INFO = 'div[data-e2e="detail-video-info"]'
# 转发按钮容器（与点赞、评论、收藏为兄弟元素，顺序：点赞(1) 评论(2) 收藏(3) 转发(4)）
VIDEO_SHARE_ICON_CONTAINER = 'div[data-e2e="video-share-icon-container"]'

# 点赞/收藏按钮 XPath 模板（基于 video-share-icon-container 的兄弟关系）
# 点赞 = share 的第 3 个 preceding-sibling；收藏 = share 的第 1 个 preceding-sibling
def like_button_xpath(video_id: str) -> str:
    """点赞按钮 XPath（detail-video-info 容器内，share 的第 3 个前兄弟）。"""
    return (
        f"//div[@data-e2e='detail-video-info' and @data-e2e-aweme-id='{video_id}']"
        f"//div[@data-e2e='video-share-icon-container']/preceding-sibling::div[3]"
    )


def favorite_button_xpath(video_id: str) -> str:
    """收藏按钮 XPath（detail-video-info 容器内，share 的第 1 个前兄弟）。"""
    return (
        f"//div[@data-e2e='detail-video-info' and @data-e2e-aweme-id='{video_id}']"
        f"//div[@data-e2e='video-share-icon-container']/preceding-sibling::div[1]"
    )


# 兼容旧版选择器（fallback）
LIKE_BUTTON = (
    "div[data-e2e='video-player-digg'], "
    "span[class*='like-icon'], "
    "div[class*='digg']"
)
# 收藏按钮
COLLECT_BUTTON = (
    "div[data-e2e='video-player-collect'], "
    "span[class*='collect-icon'], "
    "div[class*='collect']"
)
# 收藏按钮别名（兼容 like_favorite.py）
FAVORITE_BUTTON = COLLECT_BUTTON

# ========== 笔记点赞/收藏（使用 data-e2e-state 判断状态） ==========
# 父容器（限定在笔记详情页的交互区域内）
NOTE_INTERACTION_CONTAINER = "div[class*='immersive-player-switch-on-hide-interaction-area']"
# 笔记点赞按钮
NOTE_LIKE_BUTTON = f"{NOTE_INTERACTION_CONTAINER} div[data-e2e='video-player-digg']"
# 笔记收藏按钮
NOTE_COLLECT_BUTTON = f"{NOTE_INTERACTION_CONTAINER} div[data-e2e='video-player-collect']"
# 点赞状态选择器
NOTE_LIKE_NOT_DIGGED = f"{NOTE_INTERACTION_CONTAINER} div[data-e2e='video-player-digg'][data-e2e-state='video-player-no-digged']"
NOTE_LIKE_DIGGED = f"{NOTE_INTERACTION_CONTAINER} div[data-e2e='video-player-digg'][data-e2e-state='video-player-digged']"
# 收藏状态选择器
NOTE_COLLECT_NOT_COLLECTED = f"{NOTE_INTERACTION_CONTAINER} div[data-e2e='video-player-collect'][data-e2e-state='video-player-no-collect']"
NOTE_COLLECT_COLLECTED = f"{NOTE_INTERACTION_CONTAINER} div[data-e2e='video-player-collect'][data-e2e-state='video-player-collected']"

# 分享按钮
SHARE_BUTTON = (
    "div[data-e2e='video-player-share'], "
    "span[class*='share-icon']"
)
# 评论/回复输入框（通过容器定位 contenteditable）
COMMENT_INPUT_EDITABLE = "div.comment-input-inner-container"
# 评论提交按钮
COMMENT_SUBMIT_BUTTON = (
    "div[data-e2e='comment-post'], "
    "button[class*='comment-submit'], "
    "div[class*='post-comment']"
)

# ========== 评论回复（基于 data-e2e="comment-item" 结构） ==========
# 评论项容器（所有评论相关选择器需在此父元素内）
COMMENT_ITEM = 'div[data-e2e="comment-item"]'
# 评论 ID 定位：tooltip_{comment_id} 在 .comment-item-info-wrap 内
# 回复按钮：comment-item-stats-container 内 span「回复」（点击后变「回复中」）
# 回复输入框：点击回复后，评论 item 内出现 .comment-input-inner-container


def reply_button_xpath(comment_id: str) -> str:
    """回复按钮 XPath（comment-item 内，含 span「回复」的 tabindex 可点击元素）。"""
    return (
        f"//div[@data-e2e='comment-item'][.//div[@id='tooltip_{comment_id}']]"
        f"//div[@tabindex='0'][.//span[normalize-space()='回复']]"
    )


def reply_input_selector(comment_id: str) -> str:
    """回复输入框 CSS 选择器（comment-item 内 contenteditable）。"""
    return (
        f"div[data-e2e='comment-item']:has(#tooltip_{comment_id}) "
        "div.comment-input-inner-container [contenteditable='true']"
    )


# 兼容旧版
REPLY_BUTTON = "span[class*='reply'], div[class*='reply-btn']"
# 评论容器（一级评论）
PARENT_COMMENT = (
    "div[class*='comment-item'], "
    "div[class*='commentItem'], "
    "div[class*='CommentItem']"
)
# 评论容器（评论列表区域）
COMMENTS_CONTAINER = (
    "div[class*='comment-list'], "
    "div[class*='commentList'], "
    "div[data-e2e='comment-list']"
)
# 评论列表主容器（含加载中、暂无更多）
COMMENT_LIST_MAIN = 'div[data-e2e="comment-list"].comment-mainContent'
# 评论区域滚动容器（用于触发滚动加载）
COMMENT_SCROLL_CONTAINER = "div.parent-route-container.route-scroll-container"
# 加载中/暂无更多 XPath（comment-list 内通过文本区分）
COMMENT_LOADING_XPATH = (
    "//div[@data-e2e='comment-list']//*[contains(normalize-space(),'加载中')]"
)
COMMENT_NO_MORE_XPATH = (
    "//div[@data-e2e='comment-list']//*[contains(normalize-space(),'暂时没有更多评论')]"
)
# 评论到底标识
COMMENT_END_CONTAINER = (
    "div[class*='comment-end'], "
    "div[class*='no-more'], "
    "p[class*='end-text']"
)

# ========== 笔记（图文）评论区 ==========
# 笔记评论容器（所有笔记评论相关选择器需限定在此父元素内）
NOTE_COMMENT_CONTAINER = "#merge-all-comment-container"
# 笔记评论按钮（点击后切换到评论 Tab）
# 使用 data-e2e 属性定位，比文本匹配更稳定
# 父容器 class 包含 immersive-player-switch-on-hide-interaction-area
NOTE_COMMENT_BUTTON = (
    "div[class*='immersive-player-switch-on-hide-interaction-area'] "
    "div[data-e2e='feed-comment-icon']"
)
# 兼容旧版 XPath（通过文本定位）
NOTE_COMMENT_TAB_XPATH = (
    "//*[@id='detailrelatedVideoCard']//div[./div[contains(text(), '相关推荐')]]/div[starts-with(normalize-space(.), '评论')]"
)
# 笔记评论输入框（在 merge-all-comment-container 内）
NOTE_COMMENT_INPUT = "#merge-all-comment-container .comment-input-inner-container"
# 笔记评论列表
NOTE_COMMENT_LIST = "#merge-all-comment-container div[data-e2e='comment-list']"
# 笔记评论项
NOTE_COMMENT_ITEM = "#merge-all-comment-container div[data-e2e='comment-item']"
# 笔记评论加载中 XPath
NOTE_COMMENT_LOADING_XPATH = (
    "//*[@id='merge-all-comment-container']//*[contains(normalize-space(),'加载中')]"
)
# 笔记评论暂无更多 XPath
NOTE_COMMENT_NO_MORE_XPATH = (
    "//*[@id='merge-all-comment-container']//*[contains(normalize-space(),'暂时没有更多评论')]"
)
# 笔记评论区滚动容器
NOTE_COMMENT_SCROLL_CONTAINER = "#merge-all-comment-container"


def note_reply_button_xpath(comment_id: str) -> str:
    """笔记回复按钮 XPath（在 merge-all-comment-container 内定位）。"""
    return (
        f"//*[@id='merge-all-comment-container']//div[@data-e2e='comment-item'][.//div[@id='tooltip_{comment_id}']]"
        f"//div[contains(@class, 'comment-item-stats-container')]//span[normalize-space()='回复']"
    )


def note_comment_tooltip_selector(comment_id: str) -> str:
    """笔记评论 tooltip 选择器（用于定位特定评论）。"""
    return f"#merge-all-comment-container #tooltip_{comment_id}"


# ========== 用户主页 ==========
# 用户信息容器
USER_INFO_CONTAINER = "div[class*='user-info'], div[data-e2e='user-info']"
# 用户视频列表
USER_VIDEO_LIST = "div[class*='user-post'], ul[class*='video-list']"
# 用户视频卡片
USER_VIDEO_CARD = "li[class*='video-card'], div[class*='video-card']"

# ========== 发布页（创作者中心） ==========
# 创作者 TAB
CREATOR_TAB = "div.creator-tab, div[class*='creator-tab']"
# 上传入口（首次上传）
UPLOAD_INPUT = "input[type='file'][accept*='image']"
# 追加上传
FILE_INPUT = "input[type='file']"
# 图片预览
IMAGE_PREVIEW = "div[class*='image-card'], div[class*='preview-card']"
# 标题输入框
TITLE_INPUT = "input[class*='title'], input[placeholder*='标题']"
# 正文编辑器
CONTENT_EDITOR = "div[contenteditable='true'][class*='editor'], div[role='textbox']"
# 发布按钮
PUBLISH_BUTTON = "button[class*='publish'], button[class*='submit']"
# 话题标签容器
TAG_TOPIC_CONTAINER = "div[class*='topic-list'], div[class*='tag-suggest']"
# 话题标签第一项
TAG_FIRST_ITEM = "div[class*='topic-item']:first-child, li:first-child"
# 定时发布开关
SCHEDULE_SWITCH = "div[class*='schedule'] input[type='checkbox']"
# 日期时间输入
DATETIME_INPUT = "input[class*='datetime'], input[type='datetime-local']"
# 可见范围下拉
VISIBILITY_DROPDOWN = "div[class*='visibility'], select[class*='visibility']"
# 可见范围选项
VISIBILITY_OPTIONS = "div[class*='option'], li[class*='option']"
# 原创声明开关卡片
ORIGINAL_SWITCH_CARD = "div[class*='original'], div[class*='declare']"
# 原创声明开关
ORIGINAL_SWITCH = "div[class*='switch'], label[class*='switch']"
# 弹窗遮罩
POPOVER = "div[class*='popover'], div[class*='modal-mask']"
# 标题超长提示
TITLE_MAX_SUFFIX = "span[class*='title-count-error'], span[class*='over-limit']"
# 正文超长提示
CONTENT_LENGTH_ERROR = "span[class*='content-count-error'], span[class*='over-limit']"
