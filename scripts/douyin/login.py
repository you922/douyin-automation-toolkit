"""登录检查、二维码登录、手机号登录。

使用 CDP Page 方法进行元素检测，每个方法职责单一、可复用。
"""

from __future__ import annotations

import base64
import logging
import os
import tempfile
import time

from .cdp import Page
from .human import navigation_delay, sleep_random
from .selectors import (
    AVATAR_CONTAINER,
    CODE_INPUT,
    CODE_INPUT_FALLBACK,
    CODE_INPUT_VIA_CONTAINER,
    CODE_SUBMIT_BUTTON_XPATH,
    GET_CODE_BUTTON_XPATH,
    LIVE_AVATAR,
    LOGIN_BUTTON_XPATH,
    LOGIN_OTHER_ACCOUNT_XPATH,
    LOGIN_POPUP_ID_KEYWORDS,
    LOGOUT_BUTTON_XPATH,
    ONE_CLICK_LOGIN_BUTTON_XPATH,
    PHONE_INPUT,
    PHONE_INPUT_FALLBACK,
    PHONE_INPUT_VIA_CONTAINER,
    PHONE_LOGIN_TAB_XPATH,
    QRCODE_CONTAINER,
    QRCODE_IMG,
    RECEIVE_SMS_OPTION_XPATH,
    SECOND_VERIFY_CODE_INPUT,
    SECOND_VERIFY_CONTAINER,
    SECOND_VERIFY_SUBMIT_BUTTON_XPATH,
    TRUST_LOGIN_SWITCH_UNCHECK_XPATH,
)
from .urls import HOME_URL

logger = logging.getLogger(__name__)


# ========== 状态检测（原子方法） ==========


def _is_logged_in(page: Page) -> bool:
    """检查是否已登录。"""
    if page.has_element(LIVE_AVATAR):
        return True
    return page.has_element(AVATAR_CONTAINER)


def _has_login_popup(page: Page) -> bool:
    """检查是否有登录弹窗。"""
    for keyword in LOGIN_POPUP_ID_KEYWORDS:
        if page.has_element(f'[id*="{keyword}"]'):
            return True
    return False


def _get_qrcode_url(page: Page) -> str:
    """获取二维码图片 URL。"""
    selector = f"{QRCODE_CONTAINER} {QRCODE_IMG}"
    return page.get_element_attribute(selector, "src") or ""


def _click_one_click_login(page: Page) -> bool:
    """点击一键登录按钮。"""
    if not page.has_element_xpath(ONE_CLICK_LOGIN_BUTTON_XPATH):
        return False
    page.click_element_xpath(ONE_CLICK_LOGIN_BUTTON_XPATH)
    return True


def _click_logout_button(page: Page) -> bool:
    """点击退出登录按钮。"""
    if page.has_element(LIVE_AVATAR):
        page.hover_element(LIVE_AVATAR)
        sleep_random(800, 1500)
    return page.click_element_xpath(LOGOUT_BUTTON_XPATH, click_parent=True)


def _ensure_save_login_info_enabled(page: Page) -> None:
    """确保「保存登录信息」开关已开启。

    参考登出按钮逻辑：hover 头像后，在 header 菜单内查找保存登录信息开关。
    当 class 含 uncheck 时点击开启；含 check 时已开启，不做处理。
    """
    if not page.has_element(LIVE_AVATAR):
        return
    page.hover_element(LIVE_AVATAR)
    sleep_random(800, 1500)
    if page.has_element_xpath(TRUST_LOGIN_SWITCH_UNCHECK_XPATH):
        page.click_element_xpath(TRUST_LOGIN_SWITCH_UNCHECK_XPATH)
        sleep_random(500, 800)
        logger.info("已开启「保存登录信息」开关")


# ========== 输入框选择器（原子方法） ==========


def _get_phone_input_selector(page: Page) -> str | None:
    """获取可用的手机号输入框选择器。"""
    for sel in (PHONE_INPUT, PHONE_INPUT_FALLBACK, PHONE_INPUT_VIA_CONTAINER):
        if page.has_element(sel):
            return sel
    return None


def _get_code_input_selector(page: Page) -> str | None:
    """获取可用的验证码输入框选择器。"""
    if page.has_element(SECOND_VERIFY_CONTAINER) and page.has_element(SECOND_VERIFY_CODE_INPUT):
        return SECOND_VERIFY_CODE_INPUT
    for sel in (CODE_INPUT, CODE_INPUT_FALLBACK, CODE_INPUT_VIA_CONTAINER):
        if page.has_element(sel):
            return sel
    return None


# ========== 组合流程 ==========


def _navigate_and_wait(page: Page) -> None:
    """导航到首页并等待稳定。"""
    page.navigate(HOME_URL)
    page.wait_for_load()
    page.wait_dom_stable()
    sleep_random(5000, 8000)


def _ensure_on_qrcode_or_phone_page(page: Page, *, switch_account: bool) -> bool:
    """确保在二维码/手机号登录页。若有一键登录：switch_account 则切换，否则点击一键登录。返回是否在二维码/手机号页。"""
    if switch_account:
        # 切换账号：优先执行「登录其他账号」逻辑
        if page.has_element_xpath(LOGIN_OTHER_ACCOUNT_XPATH):
            if page.click_element_xpath(LOGIN_OTHER_ACCOUNT_XPATH, click_parent=True):
                sleep_random(800, 1500)
                navigation_delay()
                return page.has_element(QRCODE_CONTAINER)
            return False
        # 无「登录其他账号」按钮，可能已在二维码/手机号页
        return True
    # 非切换账号：判断是否有一键登录按钮
    if not page.has_element_xpath(ONE_CLICK_LOGIN_BUTTON_XPATH):
        return True  # 已在二维码/手机号页
    _click_one_click_login(page)
    return False  # 已点击一键登录，不在二维码页


def _ensure_phone_input_visible(page: Page) -> str | None:
    """确保手机号输入框可见，必要时切换 Tab。返回选择器或 None。"""
    sel = _get_phone_input_selector(page)
    if sel:
        return sel
    if page.click_element_xpath(PHONE_LOGIN_TAB_XPATH):
        sleep_random(800, 1500)
        navigation_delay()
    return _get_phone_input_selector(page)


# ========== 公开 API ==========


def check_login_status(page: Page) -> dict:
    """检查登录状态。

    已登录时会尝试开启「保存登录信息」开关，便于下次免验证登录。

    Args:
        page: CDP 页面对象。

    Returns:
        dict: logged_in 为 True 时已登录，含 user_info（昵称等）；否则 logged_in 为 False。
    """
    _navigate_and_wait(page)
    if _is_logged_in(page):
        _ensure_save_login_info_enabled(page)
        try:
            avatar_img = f"{LIVE_AVATAR} img"
            nickname = page.get_element_attribute(avatar_img, "alt") or ""
            return {"logged_in": True, "user_info": {"nickname": nickname.strip()}}
        except Exception:
            return {"logged_in": True, "user_info": {}}
    return {"logged_in": False}


def trigger_login(page: Page) -> dict:
    """触发登录弹窗。

    Args:
        page: CDP 页面对象。

    Returns:
        dict: success 为 True 时弹窗已打开；already_logged_in 为 True 时已登录无需操作。
    """
    _navigate_and_wait(page)
    if _is_logged_in(page):
        return {"success": False, "message": "已登录，无需触发登录", "already_logged_in": True}
    if _has_login_popup(page):
        return {"success": True, "message": "登录弹窗已打开", "already_logged_in": False}
    if page.click_element_xpath(LOGIN_BUTTON_XPATH):
        sleep_random(800, 1500)
        if _has_login_popup(page):
            return {"success": True, "message": "登录弹窗已打开", "already_logged_in": False}
    return {"success": False, "message": "无法打开登录弹窗", "already_logged_in": False}


def get_login_qrcode(page: Page, *, switch_account: bool = False) -> dict:
    """获取登录二维码或执行一键登录。

    若有一键登录：switch_account=True 时点击「登录其他账号」切换到二维码；否则直接点击一键登录。
    若有二维码：返回 qrcode_url 供保存展示。

    Args:
        page: CDP 页面对象。
        switch_account: 是否切换账号模式。为 True 时，有一键登录会切换到二维码/手机号登录页。

    Returns:
        dict: 包含以下字段：
            - qrcode_url: 二维码图片 URL，data URL 格式，可传给 save_qrcode_to_file 保存。
            - message: 提示信息。
            - login_method: "qrcode"（有二维码）、"one_click"（已点击或可一键登录）、"unknown"（无法获取）。
            - already_logged_in: 是否已登录。
    """
    r = trigger_login(page)
    if r.get("already_logged_in"):
        return {"qrcode_url": "", "message": "已登录", "login_method": "qrcode", "already_logged_in": True}
    if not r.get("success"):
        return {"qrcode_url": "", "message": r.get("message", "无法打开登录弹窗"), "login_method": "unknown", "already_logged_in": False}

    navigation_delay()

    on_qrcode_page = _ensure_on_qrcode_or_phone_page(page, switch_account=switch_account)
    if not on_qrcode_page:
        # 有一键登录：switch_account 时无法切换则提示；否则已在 _ensure 中点击
        msg = "可以一键登录" if switch_account else "已点击一键登录，请完成登录"
        return {"qrcode_url": "", "message": msg, "login_method": "one_click", "already_logged_in": False}

    if page.has_element(QRCODE_CONTAINER):
        url = _get_qrcode_url(page)
        msg = "已切换到二维码登录，请使用抖音 App 扫描" if switch_account else "请使用抖音 App 扫描二维码登录"
        return {"qrcode_url": url, "message": msg, "login_method": "qrcode", "already_logged_in": False}

    return {"qrcode_url": "", "message": "无法获取二维码，请检查页面状态或手动操作", "login_method": "unknown", "already_logged_in": False}


def save_qrcode_to_file(src: str) -> str:
    """将二维码 data URL 保存为临时 PNG 文件。

    Args:
        src: 二维码图片的 data URL（data:image/png;base64,...）或普通 URL。

    Returns:
        保存的文件绝对路径。
    """
    prefix = "data:image/png;base64,"
    if src.startswith(prefix):
        img_data = base64.b64decode(src[len(prefix) :])
    elif src.startswith("data:image/"):
        _, encoded = src.split(",", 1)
        img_data = base64.b64decode(encoded)
    else:
        raise ValueError(f"不支持的二维码格式，需要 data URL: {src[:50]}...")

    qr_dir = os.path.join(tempfile.gettempdir(), "douyin")
    os.makedirs(qr_dir, exist_ok=True)
    filepath = os.path.join(qr_dir, "login_qrcode.png")
    with open(filepath, "wb") as f:
        f.write(img_data)
    logger.info("二维码已保存: %s", filepath)
    return filepath


def check_scan_status(page: Page) -> dict:
    """检查扫码后的页面状态。

    当用户告知已完成扫码后调用。若已登录则返回成功；若出现身份验证弹窗，
    则点击「接收短信验证码」触发验证码发送，并返回 next_step 提示用户执行 verify-code。

    Args:
        page: CDP 页面对象。

    Returns:
        dict: 已登录时 {"logged_in": True, "user_info": dict}；
              需身份验证时 {"need_verify_code": True, "message": str, "next_step": str}；
              仍在等待扫码时 {"waiting_scan": True, "message": str}。
    """
    if _is_logged_in(page):
        _ensure_save_login_info_enabled(page)
        try:
            nickname = page.get_element_attribute(f"{LIVE_AVATAR} img", "alt") or ""
            user_info = {"nickname": nickname.strip()}
        except Exception:
            user_info = {}
        return {"logged_in": True, "message": "登录成功", "user_info": user_info}

    if page.has_element(SECOND_VERIFY_CONTAINER):
        if page.click_element_xpath(RECEIVE_SMS_OPTION_XPATH):
            sleep_random(1500, 2500)
            return {
                "need_verify_code": True,
                "message": "已发送验证码到您的手机，请获取验证码",
                "next_step": "获取验证码后执行 verify-code --code <验证码>",
            }
        return {
            "need_verify_code": True,
            "message": "身份验证弹窗已出现，但未能点击「接收短信验证码」，请手动操作",
            "next_step": "手动点击接收短信验证码后，执行 verify-code --code <验证码>",
        }

    if page.has_element(CODE_INPUT_FALLBACK):
        return {
            "need_verify_code": True,
            "message": "验证码输入框已出现，请查看手机接收验证码并告知",
            "next_step": "获取验证码后执行 verify-code --code <验证码>",
        }

    return {
        "waiting_scan": True,
        "message": "仍在等待扫码，请使用抖音 App 扫描二维码后再次执行 check-scan-status",
    }


def wait_for_login(page: Page, timeout: float = 120.0) -> dict:
    """等待用户扫码登录。

    若出现身份验证弹窗，会点击「接收短信验证码」并延长超时时间，继续等待用户在浏览器中输入验证码。

    Args:
        page: CDP 页面对象。
        timeout: 超时时间（秒）。

    Returns:
        dict: 登录成功时 {"logged_in": True, "message": str, "user_info": dict}；
              需验证码时 {"logged_in": False, "need_verify_code": True, "message": str}；
              超时时 {"logged_in": False, "message": str}。
    """
    deadline = time.monotonic() + timeout
    verify_clicked = False

    while time.monotonic() < deadline:
        if _is_logged_in(page):
            _ensure_save_login_info_enabled(page)
            try:
                nickname = page.get_element_attribute(f"{LIVE_AVATAR} img", "alt") or ""
                user_info = {"nickname": nickname.strip()}
            except Exception:
                user_info = {}
            return {"logged_in": True, "message": "登录成功", "user_info": user_info}

        # 身份验证弹窗：扫码后触发，点击「接收短信验证码」并延长超时
        if page.has_element(SECOND_VERIFY_CONTAINER):
            if not verify_clicked:
                if page.click_element_xpath(RECEIVE_SMS_OPTION_XPATH):
                    verify_clicked = True
                    sleep_random(1500, 2500)
            continue

        sleep_random(1500, 2500)

    return {"logged_in": False, "message": f"登录超时（{timeout}秒）"}


def send_phone_code(page: Page, phone: str) -> dict:
    """填写手机号并发送短信验证码。

    适用于无界面服务器场景，全程通过 CDP 操作，无需扫码。

    Args:
        page: CDP 页面对象。
        phone: 手机号（不含国家码，如 13800138000）。

    Returns:
        dict: success 为 True 时验证码已发送；already_logged_in 为 True 时已登录无需操作；
              失败时含 error 或 message 字段说明原因。
    """
    r = trigger_login(page)
    if r.get("already_logged_in"):
        return {"success": False, "already_logged_in": True, "message": "已登录，无需重新登录"}
    if not r.get("success"):
        return {"success": False, "already_logged_in": False, "message": r.get("message", "无法打开登录弹窗")}

    navigation_delay()

    if page.has_element_xpath(ONE_CLICK_LOGIN_BUTTON_XPATH):
        if not page.click_element_xpath(LOGIN_OTHER_ACCOUNT_XPATH, click_parent=True):
            return {"success": False, "already_logged_in": False, "error": "未找到「登录其他账号」按钮，无法切换到手机号登录"}
        sleep_random(800, 1500)
        navigation_delay()

    phone_sel = _ensure_phone_input_visible(page)
    if not phone_sel:
        return {"success": False, "already_logged_in": False, "error": "未找到手机号输入框，请检查页面是否已切换到验证码登录"}

    page.click_element(phone_sel)
    sleep_random(200, 400)
    page.type_text(phone, delay_ms=80)
    sleep_random(500, 800)

    if not page.click_element_xpath(GET_CODE_BUTTON_XPATH):
        return {"success": False, "already_logged_in": False, "error": "未找到「获取验证码」按钮，请检查页面"}

    return {
        "success": True,
        "status": "code_sent",
        "message": f"验证码已发送至 {phone[:3]}****{phone[-4:]}，请运行 verify-code --code <验证码>",
    }


def submit_phone_code(page: Page, code: str) -> bool:
    """填写短信验证码并提交登录。

    Args:
        page: CDP 页面对象。
        code: 收到的短信验证码。

    Returns:
        True 登录成功，False 失败（超时或验证码错误）。
    """
    code_sel = _get_code_input_selector(page)
    if not code_sel:
        return False

    page.click_element(code_sel)
    sleep_random(200, 400)
    page.type_text(code.strip(), delay_ms=80)
    sleep_random(500, 800)

    if page.has_element(SECOND_VERIFY_CONTAINER):
        sleep_random(300, 500)
        if not page.has_element_xpath(SECOND_VERIFY_SUBMIT_BUTTON_XPATH):
            return False
        page.click_element_xpath(SECOND_VERIFY_SUBMIT_BUTTON_XPATH)
    else:
        if not page.has_element_xpath(CODE_SUBMIT_BUTTON_XPATH):
            return False
        page.click_element_xpath(CODE_SUBMIT_BUTTON_XPATH)

    sleep_random(300, 1000)
    return True


def logout(page: Page) -> dict:
    """通过页面 UI 退出登录。

    Args:
        page: CDP 页面对象。

    Returns:
        dict: success 为 True 时退出成功；未登录时也返回 success；失败时含 message 说明原因。
    """
    _navigate_and_wait(page)
    if not _is_logged_in(page):
        return {"success": True, "message": "未登录状态，无需退出"}
    if _click_logout_button(page):
        navigation_delay()
        return {"success": True, "message": "退出登录成功"}
    return {"success": False, "message": "未找到退出登录按钮"}
