#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一 CLI 入口（scripts 目录）。

全局选项: --host, --port, --account
输出: JSON（ensure_ascii=False）**仅写入 stdout**；日志与提示 **仅写入 stderr**。
      脚本用 subprocess 解析 JSON 时请只读 stdout（或勿使用 2>&1 合并流）。
退出码: 0=成功, 1=未登录, 2=错误

使用: uv run python scripts/cli.py <command> [options]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

# 单次 skill 常有的时间限制（秒），enrich 循环接近此值即停止
ENRICH_TIME_BUDGET_SEC = 100

# 确保 scripts 目录在 path 中（account_manager、chrome_launcher、douyin 均在 scripts/ 下）
_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

# Windows 控制台默认编码不支持中文，强制 UTF-8
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# 日志一律走 stderr，stdout 仅用于 JSON，便于脚本 subprocess 解析（避免 Extra data 等混流问题）
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,
    force=True,
)
logger = logging.getLogger("dy-cli")


def _output(data: dict, exit_code: int = 0) -> None:
    """输出 JSON 并退出。"""
    print(json.dumps(data, ensure_ascii=False, indent=2))
    sys.exit(exit_code)


def _get_user_data_dir(account: str) -> str | None:
    """根据 account 返回 Chrome user-data-dir，None 表示使用 chrome_launcher 默认。

    逻辑：显式 --account 用该账号 profile；未传时用 get_default_account()，
    多账号用户配置 default 后，各入口不传 --account 也能保持 profile 一致。
    """
    from account_manager import _get_profile_dir, get_default_account

    effective = account or get_default_account()
    if not effective:
        return None
    return _get_profile_dir(effective)


def _connect(
    args: argparse.Namespace,
    reuse_page: bool = True,
    enable_second_verify_watcher: bool = True,
):
    """连接到 Chrome 并返回 (browser, page)。"""
    from chrome_launcher import ensure_chrome, has_display
    from douyin.cdp import Browser

    user_data_dir = _get_user_data_dir(args.account or "")

    if not ensure_chrome(
        port=args.port, headless=not has_display(), user_data_dir=user_data_dir
    ):
        _output(
            {"success": False, "error": "无法启动 Chrome，请检查 Chrome 是否已安装"}, 2
        )

    browser = Browser(host=args.host, port=args.port)
    browser.connect()

    if reuse_page:
        page = browser.get_existing_page()
        if page:
            if enable_second_verify_watcher:
                page.start_second_verify_watcher()
            return browser, page

    page = browser.new_page()
    if enable_second_verify_watcher:
        page.start_second_verify_watcher()
    return browser, page


def _disconnect(browser, page) -> None:
    """断开连接并停止后台监听。"""
    if page:
        page.stop_second_verify_watcher()
    browser.close()


def _connect_existing(
    args: argparse.Namespace,
    enable_second_verify_watcher: bool = True,
):
    """连接到 Chrome 并复用已有页面（用于分步登录的后续步骤）。"""
    from chrome_launcher import ensure_chrome, has_display
    from douyin.cdp import Browser

    user_data_dir = _get_user_data_dir(args.account or "")
    if not ensure_chrome(
        port=args.port, headless=not has_display(), user_data_dir=user_data_dir
    ):
        _output({"success": False, "error": "无法连接到 Chrome"}, 2)

    browser = Browser(host=args.host, port=args.port)
    browser.connect()
    page = browser.get_existing_page()
    if not page:
        _output(
            {"success": False, "error": "未找到已打开的页面，请先执行前置步骤"},
            2,
        )
    if enable_second_verify_watcher:
        page.start_second_verify_watcher()
    return browser, page


def _get_storage(args: argparse.Namespace):
    """获取 DYStorage 实例（lazy import，避免在不需要存储的命令中引入开销）。"""
    from douyin.storage import DYStorage

    return DYStorage(account=args.account or "default")


def _headless_fallback(args: argparse.Namespace) -> None:
    """Headless 模式未登录时的处理：有桌面降级到有窗口模式，无桌面直接报错提示。"""
    from chrome_launcher import has_display, restart_chrome

    if has_display():
        logger.info("Headless 模式未登录，切换到有窗口模式...")
        restart_chrome(
            port=args.port,
            headless=False,
            user_data_dir=_get_user_data_dir(args.account or ""),
        )
        _output(
            {
                "success": False,
                "error": "未登录",
                "action": "switched_to_headed",
                "message": "已切换到有窗口模式，请在浏览器中扫码登录",
            },
            1,
        )
    else:
        _output(
            {
                "success": False,
                "error": "未登录",
                "action": "login_required",
                "message": "无界面环境下请先运行 send-code --phone <手机号> 完成登录",
            },
            1,
        )


def _cmd_check_login(args: argparse.Namespace) -> None:
    """检查登录状态。"""
    browser, page = _connect(args, enable_second_verify_watcher=False)
    try:
        from douyin.login import check_login_status

        result = check_login_status(page)
        _output(result, 0 if result.get("logged_in") else 1)
    finally:
        browser.close()


def _cmd_login(args: argparse.Namespace) -> None:
    """获取登录二维码或执行一键登录。

    二维码：输出 qrcode_path 后退出，用户扫码后执行 check-scan-status。
    一键登录：点击后等待 5s，检测登录/身份验证状态；若需验证码则触发发送并提示用户执行 verify-code。
    """
    from douyin.login import check_scan_status, get_login_qrcode, save_qrcode_to_file

    browser = None
    try:
        browser, page = _connect(args, enable_second_verify_watcher=False)
        result = get_login_qrcode(page, switch_account=args.switch_account)
        if result.get("already_logged_in"):
            _output({"logged_in": True, "message": "已登录"})

        qrcode_url = result.get("qrcode_url", "")
        login_method = result.get("login_method", "unknown")

        if login_method == "qrcode" and qrcode_url:
            qrcode_path = save_qrcode_to_file(qrcode_url)
            _output(
                {
                    "success": True,
                    "qrcode_path": qrcode_path,
                    "message": "请使用抖音 App 扫描二维码。扫码完成后请告知，将执行 check-scan-status 检查页面状态",
                    "next_step": "用户扫码后执行 check-scan-status",
                }
            )
        elif login_method == "one_click":
            from douyin.human import sleep_random

            sleep_random(3000, 5500)
            status = check_scan_status(page)
            if status.get("logged_in"):
                _output(status, 0)
            elif status.get("need_verify_code"):
                _output(
                    {
                        **status,
                        "success": True,
                        "message": "已发送验证码到您的手机，请查看手机获取验证码并告知",
                        "next_step": "用户提供验证码后执行 verify-code --code <验证码>",
                    },
                    0,
                )
            else:
                _output(
                    {
                        "success": True,
                        "waiting_scan": True,
                        "message": "一键登录仍在等待确认，请用户在浏览器/手机中完成操作后告知",
                        "next_step": "用户完成后再执行 check-scan-status",
                    },
                    0,
                )
        elif login_method == "unknown":
            _output(
                {
                    "logged_in": False,
                    "message": result.get("message", "无法获取二维码"),
                },
                1,
            )
    finally:
        if browser:
            browser.close()


def _cmd_check_scan_status(args: argparse.Namespace) -> None:
    """检查扫码后的页面状态。用户告知已完成扫码后执行。

    若已登录则返回成功；若需身份验证，会点击「接收短信验证码」并输出提示，
    用户获取验证码后执行 verify-code --code <验证码>。
    """
    from douyin.login import check_scan_status

    browser = None
    page = None
    try:
        browser, page = _connect_existing(args, enable_second_verify_watcher=False)
        result = check_scan_status(page)
        if result.get("logged_in"):
            _output(result, 0)
        elif result.get("need_verify_code"):
            _output(
                {
                    **result,
                    "success": True,
                },
                0,
            )
        else:
            _output(result, 0)
    except Exception as e:
        logger.error("检查页面状态失败: %s", e, exc_info=True)
        _output({"success": False, "error": str(e)}, 2)
    finally:
        if browser:
            browser.close()


def _cmd_send_code(args: argparse.Namespace) -> None:
    """分步登录：填写手机号并发送验证码。复用 login 页面，保持页面不关闭。

    输出后立即退出，由 agent 获取验证码后执行 verify-code --code <验证码>。
    """
    from douyin.login import send_phone_code

    browser = None
    try:
        # _connect(reuse_page=True) 复用 login 已打开的页面
        browser, page = _connect(args, enable_second_verify_watcher=False)
        result = send_phone_code(page, args.phone)
        if result.get("already_logged_in"):
            _output(
                {
                    "logged_in": True,
                    "message": result.get("message", "已登录，无需重新登录"),
                }
            )
            return
        if result.get("success"):
            _output(
                {
                    **result,
                    "next_step": "请让用户查看手机获取验证码，然后执行 verify-code --code <验证码>",
                }
            )
            return
        _output(
            {
                "success": False,
                "error": result.get("error", result.get("message", "未知错误")),
            },
            2,
        )
    except NotImplementedError as e:
        _output({"success": False, "error": str(e)}, 2)
    finally:
        # 只断开控制连接，不关闭页面，供 verify-code 复用
        if browser:
            browser.close()


def _cmd_verify_code(args: argparse.Namespace) -> None:
    """分步登录第二步：在已有页面上填写验证码并提交。"""
    from douyin.login import submit_phone_code

    browser = None
    page = None
    try:
        browser, page = _connect_existing(args, enable_second_verify_watcher=False)
        success = submit_phone_code(page, args.code)
        print(
            json.dumps(
                {
                    "logged_in": success,
                    "message": "登录成功" if success else "验证码错误或超时",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        browser.close()


def _cmd_logout(args: argparse.Namespace) -> None:
    """退出登录。"""
    browser, page = _connect(args, enable_second_verify_watcher=False)
    try:
        from douyin.login import logout

        result = logout(page)
        _output(result, 0 if result.get("success") else 2)
    finally:
        browser.close()


def _cmd_close_browser(args: argparse.Namespace) -> None:
    """关闭当前浏览器 tab 并断开 CDP 连接，释放 agent 侧资源。Chrome 进程仍会保留。完成用户请求后应调用此命令收尾。"""
    from chrome_launcher import ensure_chrome, has_display
    from douyin.cdp import Browser

    user_data_dir = _get_user_data_dir(args.account or "")
    if not ensure_chrome(
        port=args.port, headless=not has_display(), user_data_dir=user_data_dir
    ):
        _output({"success": False, "error": "Chrome 未启动"}, 2)

    browser = Browser(host=args.host, port=args.port)
    browser.connect()
    page = browser.get_existing_page()
    if page:
        browser.close_page(page)
    browser.close()
    _output({"success": True, "message": "已关闭浏览器 tab"})


def _cmd_chrome(args: argparse.Namespace) -> None:
    """Chrome 进程管理（start/stop/status/restart）。"""
    from chrome_launcher import (
        find_chrome,
        is_chrome_running,
        kill_chrome,
        launch_chrome,
        restart_chrome,
    )

    action = args.chrome_action
    if action == "start":
        if is_chrome_running():
            print("Chrome 已在运行")
        else:
            chrome_path = find_chrome()
            if not chrome_path:
                print("未找到 Chrome，请安装或设置 CHROME_BIN 环境变量")
                sys.exit(1)
            print(f"启动 Chrome: {chrome_path}")
            launch_chrome()
            print("Chrome 已启动")

    elif action == "stop":
        if is_chrome_running():
            kill_chrome()
            print("Chrome 已关闭")
        else:
            print("Chrome 未在运行")

    elif action == "status":
        if is_chrome_running():
            print("Chrome 正在运行")
        else:
            print("Chrome 未在运行")

    elif action == "restart":
        restart_chrome()
        print("Chrome 已重启")


# ===== 热榜命令 =====


def _cmd_get_hot_list(args: argparse.Namespace) -> None:
    """获取抖音实时热榜（需浏览器）。

    热搜词作为 keyword 关联到 videos 表，关联视频（aweme_infos）自动存入 videos 表。
    """
    from datetime import datetime, timezone

    from douyin.hot_list import fetch_hot_list

    started_at = datetime.now(timezone.utc).isoformat()
    browser, page = _connect(args)
    storage = _get_storage(args)
    try:
        topics, feeds = fetch_hot_list(page, count=args.count)
        result_data: dict = {
            "success": True,
            "count": len(topics),
            "topics": [t.to_dict() for t in topics],
        }

        if feeds:
            result_data["related_videos"] = len(feeds)

        if not args.no_save:
            save_result = storage.save_hot_topics(topics)

            video_count = 0
            if feeds:
                try:
                    storage.upsert_videos_from_feeds(feeds, keyword="热榜关联")
                    video_count = len(feeds)
                except Exception as ve:
                    logger.warning("保存热榜关联视频失败: %s", ve)

            storage.log_hot_fetch(
                found=save_result["found"],
                new_count=save_result["new"],
                video_count=video_count,
                status=0 if save_result["errors"] == 0 else 1,
                started_at=started_at,
            )
            result_data["saved"] = save_result
            if video_count:
                result_data["saved"]["video_count"] = video_count

        _output(result_data)
    except Exception as e:
        logger.error("获取热榜失败: %s", e, exc_info=True)
        if not args.no_save:
            storage.log_hot_fetch(
                found=0, new_count=0, status=2, error=str(e), started_at=started_at
            )
        _output({"success": False, "error": str(e)}, 2)
    finally:
        _disconnect(browser, page)

def _cmd_query_hot_list(args: argparse.Namespace) -> None:
    """查询本地缓存热榜话题（无需浏览器）。"""
    storage = _get_storage(args)
    topics = storage.query_hot_topics(
        date_str=args.date if args.date else None,
        limit=args.limit,
    )

    _output({
        "success": True,
        "count": len(topics),
        "date": args.date or "latest",
        "topics": topics,
    })

def _cmd_hot_list_stats(args: argparse.Namespace) -> None:
    """热榜统计信息（无需浏览器）。"""
    storage = _get_storage(args)
    stats = storage.query_hot_stats(days=args.days)
    _output({
        "success": True,
        "days": args.days,
        "stats": stats,
    })

def _cmd_hot_list_logs(args: argparse.Namespace) -> None:
    """热榜抓取日志（无需浏览器）。"""
    storage = _get_storage(args)
    logs = storage.query_hot_logs(limit=args.limit)
    _output({
        "success": True,
        "count": len(logs),
        "logs": logs,
    })

def _cmd_search_videos(args: argparse.Namespace) -> None:
    """搜索视频。仅返回列表，不获取详情。需详情时由模型调用 get-video-detail。

    与小红书 search-feeds 设计一致：保持原子性，模型推断是否获取详情及数量。
    """
    browser, page = _connect(args)
    try:
        from douyin.search import search_videos

        feeds = search_videos(
            page,
            keyword=args.keyword,
            max_results=args.count,
            sort_by=args.sort_by,
        )
        videos = [f.to_dict() for f in feeds]
        try:
            _get_storage(args).upsert_videos_from_feeds(feeds, keyword=args.keyword)
        except Exception as e:
            logger.warning("[storage] 写入失败: %s", e)
        _output(
            {
                "videos": videos,
                "count": len(videos),
                "keyword": args.keyword,
            }
        )
    finally:
        _disconnect(browser, page)


def _fetch_subtitle_and_transcript(
    page,
    video_url: str,
    video_id: str,
    video_info: dict | None,
    *,
    fetch_subtitles: bool = True,
    fetch_transcript: bool = True,
    output_dir: str | None = None,
    cookie_string: str = "",
    whisper_model: str = "base",
) -> tuple[dict, dict]:
    """获取字幕和转录。get-video-detail 与 enrich 共用。

    转录策略：必剪 ASR 优先（云端、快、中文效果好）→ Whisper 兜底（本地、离线可用）。

    返回 (subtitle_result, transcript_result)，结构同 get-video-detail 的 output["subtitle"]/output["transcript"]。
    """
    from douyin.bcut_asr import transcribe_video_with_bcut
    from douyin.transcriber import process_video
    from douyin.video_detail import (
        DEFAULT_OUTPUT_DIR,
        download_video,
        extract_subtitles,
    )

    out_dir = output_dir or str(DEFAULT_OUTPUT_DIR)
    duration_ms = (video_info or {}).get("duration") or 0
    duration_sec = duration_ms / 1000.0 if duration_ms > 1000 else duration_ms

    # 1. 字幕
    if not fetch_subtitles:
        sub_result = {
            "success": False,
            "subtitle_text": "",
            "subtitle_path": None,
            "subtitle_type": None,
            "error": "已跳过",
        }
    else:
        try:
            sub_result = extract_subtitles(
                page,
                video_url,
                output_dir=output_dir,
                cookie_string=cookie_string,
                subtitle_infos=(video_info or {}).get("subtitle_infos"),
                play_url=(video_info or {}).get("play_url"),
                duration_ms=(video_info or {}).get("duration"),
            )
            sub_result = {
                "success": sub_result.get("success", False),
                "subtitle_text": sub_result.get("subtitle_text", ""),
                "subtitle_path": sub_result.get("subtitle_path"),
                "subtitle_type": sub_result.get("subtitle_type"),
                "error": sub_result.get("error"),
            }
        except Exception as e:
            sub_result = {
                "success": False,
                "subtitle_text": "",
                "subtitle_path": None,
                "subtitle_type": None,
                "error": str(e),
            }

    # 2. 转录：必剪 ASR 优先 → Whisper 兜底（>16s 跳过 Whisper，避免超时）
    if not fetch_transcript:
        trans_result = {
            "success": False,
            "transcript": "",
            "transcript_path": None,
            "model": None,
            "error": "已跳过",
        }
    else:
        play_url = (video_info or {}).get("play_url")
        trans_result = _do_transcript_with_fallback(
            page=page,
            video_url=video_url,
            video_id=video_id,
            play_url=play_url,
            duration_sec=duration_sec,
            out_dir=out_dir,
            whisper_model=whisper_model,
            download_video_fn=download_video,
            process_video_fn=process_video,
            transcribe_bcut_fn=transcribe_video_with_bcut,
        )

    return sub_result, trans_result


def _do_transcript_with_fallback(
    *,
    page,
    video_url: str,
    video_id: str,
    play_url: str | None,
    duration_sec: float,
    out_dir: str,
    whisper_model: str,
    download_video_fn,
    process_video_fn,
    transcribe_bcut_fn,
) -> dict:
    """转录核心逻辑：必剪 ASR 优先 → Whisper 兜底。

    必剪 ASR：云端处理，中文效果好，16s 视频通常 3-5s 出结果。
    Whisper：本地推理，离线可用，模型已缓存避免重复加载。
    """
    # Step 1: 下载视频（必剪和 Whisper 都需要视频/音频文件）
    filepath = download_video_fn(
        page, video_url, output_dir=out_dir, filename=video_id, play_url=play_url
    )
    if not filepath:
        return {
            "success": False,
            "transcript": "",
            "transcript_path": None,
            "model": None,
            "error": "视频下载失败，无法转录",
        }

    # Step 2: 必剪 ASR 优先（云端、快、中文效果好）
    try:
        logger.info("尝试必剪 ASR 转录（优先）...")
        bcut_result = transcribe_bcut_fn(str(filepath), output_dir=out_dir)
        if bcut_result.get("success") and bcut_result.get("text", "").strip():
            logger.info("必剪 ASR 转录成功，字数: %d", len(bcut_result["text"]))
            return {
                "success": True,
                "transcript": bcut_result["text"],
                "transcript_path": bcut_result.get("output_file"),
                "model": "bcut-asr",
                "error": None,
            }
        bcut_error = bcut_result.get("error", "结果为空")
        logger.info("必剪 ASR 未成功: %s，降级 Whisper", bcut_error)
    except Exception as bcut_exc:
        bcut_error = str(bcut_exc)
        logger.info("必剪 ASR 异常: %s，降级 Whisper", bcut_error)

    # Step 3: Whisper 兜底（>16s 跳过，避免长视频超时）
    if duration_sec > 16:
        return {
            "success": False,
            "transcript": "",
            "transcript_path": None,
            "model": None,
            "error": f"必剪 ASR 失败（{bcut_error}），视频时长 {duration_sec:.1f}s > 16s，跳过 Whisper",
        }

    def _whisper_transcript() -> dict:
        process_result = process_video_fn(
            filepath, output_dir=out_dir, whisper_model=whisper_model
        )
        return {
            "success": process_result.get("success", False),
            "transcript": process_result.get("transcript", ""),
            "transcript_path": process_result.get("transcript_path"),
            "model": process_result.get("model"),
            "error": process_result.get("error"),
        }

    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_whisper_transcript)
    try:
        trans_result = future.result(timeout=30)
        executor.shutdown(wait=True)
        return trans_result
    except FuturesTimeoutError:
        logger.warning("Whisper 转录 30s 超时")
        executor.shutdown(wait=False)
        return {
            "success": False,
            "transcript": "",
            "transcript_path": None,
            "model": f"whisper-{whisper_model}",
            "error": f"必剪 ASR 失败（{bcut_error}），Whisper 30s 超时",
        }
    except Exception as whisper_exc:
        executor.shutdown(wait=True)
        logger.warning("Whisper 转录异常: %s", whisper_exc)
        return {
            "success": False,
            "transcript": "",
            "transcript_path": None,
            "model": None,
            "error": f"必剪 ASR 失败（{bcut_error}），Whisper 异常: {whisper_exc}",
        }


def _cmd_detect_type(args: argparse.Namespace) -> None:
    """探测 aweme 类型（视频/笔记），不导航页面。

    通过 API 查询 aweme_type，返回类型信息和正确的详情页 URL。
    模型可先调用此命令探测类型，再决定用 get-video-detail 还是 get-video-detail --aweme-type note。
    """
    browser, page = _connect(args)
    try:
        from douyin.feed_detail import detect_aweme_type

        result = detect_aweme_type(page, args.aweme_id)
        _output(result, 0 if result.get("success") else 2)
    finally:
        _disconnect(browser, page)


def _cmd_get_video_detail(args: argparse.Namespace) -> None:
    """获取视频/笔记详情。

    视频类型：补充字幕、Whisper 转录等列表页没有的字段。
    笔记类型（图文）：获取标题、内容、图片列表、评论，跳过字幕和转录。
    类型判断：优先使用 --aweme-type 参数（"note"/"video"），其次从 API 返回的 aweme_type 自动识别。
    注意：若需批量获取多个详情，应在每次调用之间增加  3.0～5.0 秒延迟。
    """
    import time as _time

    t_cmd_start = _time.monotonic()
    browser, page = _connect(args)
    t_connect = _time.monotonic()
    logger.info("[耗时] connect: %.1fs", t_connect - t_cmd_start)

    try:
        from douyin.cookies import cookies_to_string, load_cookies
        from douyin.feed_detail import fetch_comments, get_video_info
        from douyin.urls import make_aweme_detail_url, make_note_detail_url, make_video_detail_url

        # 根据 --aweme-type 参数决定 URL 类型（"note" 或 "video"，默认 "video"）
        is_note_flag = getattr(args, "aweme_type", "video") == "note"
        if is_note_flag:
            detail_url = make_note_detail_url(args.video_id)
        else:
            detail_url = make_video_detail_url(args.video_id)

        t0 = _time.monotonic()
        result = get_video_info(
            page,
            detail_url,
            fetch_comments_parallel=args.max_comments,
        )
        t_get_info = _time.monotonic()
        logger.info("[耗时] get_video_info+comments 合计: %.1fs", t_get_info - t0)

        if isinstance(result, tuple):
            video_info, comments = result
        else:
            video_info = result
            comments = fetch_comments(page, detail_url, max_comments=args.max_comments)

        # 自动检测笔记类型：API 返回的 aweme_type == 68 或用户指定 --aweme-type note
        is_note = is_note_flag or video_info.get("aweme_type") == 68

        if is_note:
            logger.info("检测到笔记（图文）类型，跳过字幕和转录")
            sub_result = {
                "success": False,
                "subtitle_text": "",
                "subtitle_path": None,
                "subtitle_type": None,
                "error": "笔记类型，无需字幕",
            }
            trans_result = {
                "success": False,
                "transcript": "",
                "transcript_path": None,
                "model": None,
                "error": "笔记类型，无需转录",
            }
        else:
            cookie_string = cookies_to_string(load_cookies(args.account or "default"))
            _ts = _time.monotonic()
            sub_result, trans_result = _fetch_subtitle_and_transcript(
                page,
                detail_url,
                args.video_id,
                video_info,
                fetch_subtitles=not getattr(args, "no_fetch_subtitles", False),
                fetch_transcript=not getattr(args, "no_fetch_transcript", False),
                output_dir=args.output_dir or None,
                cookie_string=cookie_string,
                whisper_model=getattr(args, "whisper_model", "base"),
            )
            logger.info("[耗时] subtitle+transcript: %.1fs", _time.monotonic() - _ts)

        try:
            storage = _get_storage(args)
            storage.upsert_video(video_info)
            storage.upsert_comments(comments, args.video_id)
        except Exception as e:
            logger.warning("[storage] 写入失败: %s", e)

        logger.info("[耗时] 总耗时: %.1fs", _time.monotonic() - t_cmd_start)

        output_data = {
            "video": video_info,
            "comments": comments,
            "comment_count": video_info.get("comment_count", 0),
            "comments_returned": len(comments),
            "subtitle": sub_result,
            "transcript": trans_result,
        }
        if is_note:
            output_data["is_note"] = True
            output_data["images"] = video_info.get("images", [])
        _output(output_data)
    finally:
        _disconnect(browser, page)


def _cmd_download_video(args: argparse.Namespace) -> None:
    """下载视频文件。"""
    browser, page = _connect(args)
    try:
        from douyin.urls import make_video_detail_url
        from douyin.video_detail import download_video

        video_url = make_video_detail_url(args.video_id)
        filepath = download_video(
            page,
            video_url,
            output_dir=args.output_dir or None,
            filename=args.filename or None,
        )
        if filepath:
            _output(
                {
                    "success": True,
                    "video_id": args.video_id,
                    "file_path": filepath,
                }
            )
        else:
            _output({"success": False, "error": "视频下载失败"}, 2)
    finally:
        _disconnect(browser, page)


def _cmd_extract_subtitles(args: argparse.Namespace) -> None:
    """提取视频字幕。"""
    browser, page = _connect(args)
    try:
        from douyin.cookies import cookies_to_string, load_cookies
        from douyin.urls import make_video_detail_url
        from douyin.video_detail import extract_subtitles

        video_url = make_video_detail_url(args.video_id)
        cookie_string = cookies_to_string(load_cookies(args.account or "default"))
        result = extract_subtitles(
            page,
            video_url,
            output_dir=args.output_dir or None,
            cookie_string=cookie_string,
        )
        _output(result, 0 if result.get("success") else 2)
    finally:
        _disconnect(browser, page)


def _enrich_feed_with_video_detail(
    page,
    feed_dict: dict,
    video_url: str,
    video_id: str,
    *,
    fetch_subtitles: bool = True,
    fetch_transcript: bool = True,
    output_dir: str | None = None,
    cookie_string: str = "",
) -> dict:
    """复用 get-video-detail 的字幕/转录逻辑，补充视频/笔记详情到 feed_dict。

    笔记类型（aweme_type == 68）自动跳过字幕和转录。
    """
    from douyin.feed_detail import get_video_info

    detail = get_video_info(page, video_url)
    if detail:
        for k, v in detail.items():
            if k == "subtitle_infos":
                continue
            if v is not None and v != "" and v != 0:
                feed_dict[k] = v

    # 笔记类型跳过字幕和转录
    is_note = feed_dict.get("aweme_type") == 68 or (detail and detail.get("aweme_type") == 68)
    if is_note:
        logger.info("笔记类型 %s，跳过字幕和转录", video_id)
        return feed_dict

    sub_result, trans_result = _fetch_subtitle_and_transcript(
        page,
        video_url,
        video_id,
        detail,
        fetch_subtitles=fetch_subtitles,
        fetch_transcript=fetch_transcript,
        output_dir=output_dir,
        cookie_string=cookie_string,
        whisper_model="base",
    )

    if sub_result.get("success") and sub_result.get("subtitle_text"):
        feed_dict["subtitle_text"] = sub_result.get("subtitle_text", "")
        feed_dict["subtitle_path"] = sub_result.get("subtitle_path")
        feed_dict["subtitle_type"] = sub_result.get("subtitle_type", "")
    elif sub_result.get("error") and sub_result["error"] != "已跳过":
        logger.warning("提取字幕失败 %s: %s", video_id, sub_result.get("error"))

    if trans_result.get("success") and trans_result.get("transcript"):
        feed_dict["transcript"] = trans_result.get("transcript", "")
        feed_dict["transcript_path"] = trans_result.get("transcript_path")
    elif trans_result.get("error") and "跳过" not in (trans_result.get("error") or ""):
        logger.warning("转录失败 %s: %s", video_id, trans_result.get("error"))

    return feed_dict


def _cmd_user_profile(args: argparse.Namespace) -> None:
    """获取用户主页信息。默认对前 max_videos 条视频补充详情（含 get-video-detail 逻辑：点赞、评论、封面、字幕、转录）。"""
    browser, page = _connect(args)
    try:
        from douyin.human import sleep_random
        from douyin.user_profile import get_user_profile
        from douyin.urls import make_aweme_detail_url, make_video_detail_url

        max_videos = getattr(args, "max_videos", 10)
        result = get_user_profile(page, args.user_id, max_videos=max_videos)
        output = result.to_dict()

        fetch_subtitles = not getattr(args, "no_fetch_subtitles", False)
        fetch_transcript = not getattr(args, "no_fetch_transcript", False)
        output_dir = getattr(args, "output_dir") or None

        if not getattr(args, "no_enrich_details", False) and result.feeds:
            from douyin.cookies import cookies_to_string, load_cookies

            cookie_string = cookies_to_string(load_cookies(args.account or "default"))
            max_enrich = getattr(args, "max_enrich", 5)
            enriched_count = 0
            t_enrich_start = time.monotonic()
            for i, feed in enumerate(result.feeds):
                if not feed.video_id:
                    continue
                if enriched_count >= max_enrich:
                    break
                if time.monotonic() - t_enrich_start >= ENRICH_TIME_BUDGET_SEC:
                    logger.info("已接近时间限制，停止补充详情（已处理 %d 条）", enriched_count)
                    break
                try:
                    detail_url = make_aweme_detail_url(feed.video_id, feed.aweme_type)
                    output["feeds"][i] = _enrich_feed_with_video_detail(
                        page,
                        output["feeds"][i].copy(),
                        detail_url,
                        feed.video_id,
                        fetch_subtitles=fetch_subtitles,
                        fetch_transcript=fetch_transcript,
                        output_dir=output_dir,
                        cookie_string=cookie_string,
                    )
                    enriched_count += 1
                    if enriched_count < max_enrich and i < len(result.feeds) - 1:
                        sleep_random(3000, 5000)
                except Exception as e:
                    logger.warning("获取视频详情失败 %s: %s", feed.video_id, e)

        try:
            _get_storage(args).upsert_videos_from_feeds(result.feeds)
        except Exception as e:
            logger.warning("[storage] 写入失败: %s", e)

        _output(output)
    finally:
        _disconnect(browser, page)

def _cmd_my_profile(args: argparse.Namespace) -> None:
    """获取当前登录账号主页。默认对前 max_videos 条视频补充详情（含 get-video-detail 逻辑：点赞、评论、封面、字幕、转录）。"""
    browser, page = _connect(args)
    try:
        from douyin.human import sleep_random
        from douyin.user_profile import get_my_profile
        from douyin.urls import make_aweme_detail_url, make_video_detail_url

        max_videos = getattr(args, "max_videos", 10)
        result = get_my_profile(page, max_videos=max_videos)
        output = result.to_dict()

        fetch_subtitles = not getattr(args, "no_fetch_subtitles", False)
        fetch_transcript = not getattr(args, "no_fetch_transcript", False)
        output_dir = getattr(args, "output_dir") or None

        if not getattr(args, "no_enrich_details", False) and result.feeds:
            from douyin.cookies import cookies_to_string, load_cookies

            cookie_string = cookies_to_string(load_cookies(args.account or "default"))
            max_enrich = getattr(args, "max_enrich", 5)
            enriched_count = 0
            t_enrich_start = time.monotonic()
            for i, feed in enumerate(result.feeds):
                if not feed.video_id:
                    continue
                if enriched_count >= max_enrich:
                    break
                if time.monotonic() - t_enrich_start >= ENRICH_TIME_BUDGET_SEC:
                    logger.info("已接近时间限制，停止补充详情（已处理 %d 条）", enriched_count)
                    break
                try:
                    detail_url = make_aweme_detail_url(feed.video_id, feed.aweme_type)
                    output["feeds"][i] = _enrich_feed_with_video_detail(
                        page,
                        output["feeds"][i].copy(),
                        detail_url,
                        feed.video_id,
                        fetch_subtitles=fetch_subtitles,
                        fetch_transcript=fetch_transcript,
                        output_dir=output_dir,
                        cookie_string=cookie_string,
                    )
                    enriched_count += 1
                    if enriched_count < max_enrich and i < len(result.feeds) - 1:
                        sleep_random(3000, 5000)
                except Exception as e:
                    logger.warning("获取视频详情失败 %s: %s", feed.video_id, e)

        try:
            storage = _get_storage(args)
            storage.upsert_videos_from_feeds(result.feeds)
            video_ids = [f.video_id for f in result.feeds if f.video_id]
            if video_ids:
                storage.mark_videos_mine(video_ids)
            my_author_id = (
                (result.feeds[0].author_id if result.feeds else "")
                or (result.user_basic_info.user_id if result.user_basic_info else "")
                or (result.user_basic_info.sec_uid if result.user_basic_info else "")
                or ""
            )
            if my_author_id:
                storage.set_my_identity(my_author_id)
                storage.mark_comments_mine(my_author_id)
        except Exception as e:
            logger.warning("[storage] 写入失败: %s", e)

        _output(output)
    finally:
        _disconnect(browser, page)


def _cmd_post_comment(args: argparse.Namespace) -> None:
    """发表评论（视频或笔记）。"""
    video_id = getattr(args, "video_id", None)
    note_id = getattr(args, "note_id", None)

    if not video_id and not note_id:
        _output({"success": False, "error": "必须提供 --video-id 或 --note-id"}, 2)
        return

    browser, page = _connect(args)
    try:
        if note_id:
            from douyin.comment import post_note_comment
            result = post_note_comment(page, note_id, args.content)
        else:
            from douyin.comment import post_comment
            result = post_comment(page, video_id, args.content)
        _output(result, 0 if result.get("success") else 2)
    finally:
        _disconnect(browser, page)


def _cmd_reply_comment(args: argparse.Namespace) -> None:
    """回复评论（视频或笔记）。"""
    video_id = getattr(args, "video_id", None)
    note_id = getattr(args, "note_id", None)

    if not video_id and not note_id:
        _output({"success": False, "error": "必须提供 --video-id 或 --note-id"}, 2)
        return

    browser, page = _connect(args)
    try:
        if note_id:
            from douyin.comment import reply_note_comment
            result = reply_note_comment(page, note_id, args.comment_id, args.content)
        else:
            from douyin.comment import reply_comment
            result = reply_comment(page, video_id, args.comment_id, args.content)
        _output(result, 0 if result.get("success") else 2)
    finally:
        _disconnect(browser, page)


def _cmd_like_video(args: argparse.Namespace) -> None:
    """点赞/取消点赞。"""
    browser, page = _connect(args)
    try:
        from douyin.like_favorite import like_video

        result = like_video(page, args.video_id, unlike=args.unlike)
        _output(result, 0 if result.get("success") else 2)
    finally:
        _disconnect(browser, page)


def _cmd_favorite_video(args: argparse.Namespace) -> None:
    """收藏/取消收藏。"""
    browser, page = _connect(args)
    try:
        from douyin.like_favorite import favorite_video

        result = favorite_video(page, args.video_id, unfavorite=args.unfavorite)
        _output(result, 0 if result.get("success") else 2)
    finally:
        _disconnect(browser, page)


def _cmd_query_videos(args: argparse.Namespace) -> None:
    """查询本地缓存视频。"""
    storage = _get_storage(args)
    videos = storage.query_videos(
        mine_only=args.mine,
        keyword=args.keyword or None,
        limit=args.limit,
        offset=args.offset,
    )
    _output({"videos": videos, "count": len(videos)})


def _cmd_query_comments(args: argparse.Namespace) -> None:
    """查询本地缓存评论。"""
    storage = _get_storage(args)
    comments = storage.query_comments(
        video_id=args.video_id or None,
        mine_only=args.mine,
        limit=args.limit,
    )
    _output({"comments": comments, "count": len(comments)})


def _cmd_search_local(args: argparse.Namespace) -> None:
    """本地全文 LIKE 检索。"""
    storage = _get_storage(args)
    results = storage.search_local(
        query=args.query,
        target=args.target,
        limit=args.limit,
    )
    _output({"results": results, "query": args.query, "count": len(results)})


def _cmd_trend_analysis(args: argparse.Namespace) -> None:
    """关键词互动趋势分析。"""
    storage = _get_storage(args)
    result = storage.trend_analysis(
        keyword=args.keyword,
        days=args.days,
    )
    _output(result)


def _read_file_content(file_path: str) -> str:
    """读取文件内容并去除首尾空白。"""
    with open(file_path, encoding="utf-8") as f:
        return f.read().strip()


def _cmd_check_video_format(args: argparse.Namespace) -> None:
    """检测视频格式（大小/时长/分辨率）。"""
    from douyin.publish import check_video_format

    result = check_video_format(args.video_file, args.type)
    _output(result, 0 if result.get("valid") else 2)


def _cmd_fill_publish_video(args: argparse.Namespace) -> None:
    """填写普通视频发布表单（不点击发布）。"""
    browser, page = _connect(args)
    try:
        from douyin.publish import fill_publish_video

        title = _read_file_content(args.title_file) if args.title_file else args.title
        content = (
            _read_file_content(args.content_file) if args.content_file else args.content
        )

        result = fill_publish_video(
            page,
            title=title,
            content=content,
            video_file=args.video_file,
            location=args.location,
            product=args.product,
            hotspot=args.hotspot,
            visibility=args.visibility,
            allow_save=args.allow_save,
        )
        _output(result, 0 if result.get("success") else 2)
    finally:
        browser.close()


def _cmd_fill_publish_vr(args: argparse.Namespace) -> None:
    """填写全景视频发布表单（不点击发布）。"""
    browser, page = _connect(args)
    try:
        from douyin.publish import fill_publish_vr

        title = _read_file_content(args.title_file) if args.title_file else args.title
        content = (
            _read_file_content(args.content_file) if args.content_file else args.content
        )

        result = fill_publish_vr(
            page,
            title=title,
            content=content,
            video_file=args.video_file,
            vr_format=args.vr_format,
            cover=args.cover,
            location=args.location,
            product=args.product,
            hotspot=args.hotspot,
            visibility=args.visibility,
            allow_save=args.allow_save,
        )
        _output(result, 0 if result.get("success") else 2)
    finally:
        browser.close()


def _cmd_fill_publish_image(args: argparse.Namespace) -> None:
    """填写图文发布表单（不点击发布）。"""
    browser, page = _connect(args)
    try:
        from douyin.publish import fill_publish_image

        title = _read_file_content(args.title_file) if args.title_file else args.title
        content = (
            _read_file_content(args.content_file) if args.content_file else args.content
        )

        result = fill_publish_image(
            page,
            title=title,
            content=content,
            images=args.images,
            topics=args.topics or [],
            music=args.music,
            location=args.location,
            product=args.product,
            hotspot=args.hotspot,
            visibility=args.visibility,
            allow_save=args.allow_save,
        )
        _output(result, 0 if result.get("success") else 2)
    finally:
        browser.close()


def _cmd_fill_publish_article(args: argparse.Namespace) -> None:
    """填写文章发布表单（不点击发布）。"""
    browser, page = _connect(args)
    try:
        from douyin.publish import fill_publish_article

        title = _read_file_content(args.title_file) if args.title_file else args.title
        content = (
            _read_file_content(args.content_file) if args.content_file else args.content
        )
        summary = (
            _read_file_content(args.summary_file) if args.summary_file else args.summary
        )

        result = fill_publish_article(
            page,
            title=title,
            content=content,
            summary=summary,
            cover=args.cover,
            topics=args.topics or [],
            music=args.music,
            visibility=args.visibility,
        )
        _output(result, 0 if result.get("success") else 2)
    finally:
        browser.close()


def _cmd_click_publish(args: argparse.Namespace) -> None:
    """点击发布按钮，监听发布接口返回结果。"""
    browser, page = _connect(args)
    try:
        from douyin.publish import click_publish

        result = click_publish(page)
        _output(result, 0 if result.get("success") else 2)
    finally:
        browser.close()


def _cmd_publish_video(args: argparse.Namespace) -> None:
    """普通视频一步到位发布。"""
    browser, page = _connect(args)
    try:
        from douyin.publish import check_video_format, fill_publish_video, click_publish

        # 先检测视频格式
        format_result = check_video_format(args.video_file, "normal")
        if not format_result.get("valid"):
            _output(format_result, 2)

        title = _read_file_content(args.title_file) if args.title_file else args.title
        content = (
            _read_file_content(args.content_file) if args.content_file else args.content
        )

        fill_result = fill_publish_video(
            page,
            title=title,
            content=content,
            video_file=args.video_file,
            location=args.location,
            product=args.product,
            hotspot=args.hotspot,
            visibility=args.visibility,
            allow_save=args.allow_save,
        )
        if not fill_result.get("success"):
            _output(fill_result, 2)

        result = click_publish(page)
        _output(result, 0 if result.get("success") else 2)
    finally:
        browser.close()


# ===== 素材管理命令 =====


def _cmd_material_check(args: argparse.Namespace) -> None:
    """检查素材管理依赖是否安装。"""
    from material import check_dependencies

    result = check_dependencies()
    _output(result)


def _cmd_material_download_model(args: argparse.Namespace) -> None:
    """下载本地 Embedding 模型（BAAI/bge-small-zh-v1.5）。"""
    from material import download_embedding_model

    result = download_embedding_model()
    _output(result, 0 if result.get("success") else 2)


def _cmd_material_config(args: argparse.Namespace) -> None:
    """查看或修改素材管理配置。"""
    from material import get_config, update_config

    if any(
        [
            args.api_key,
            args.model_name,
            args.base_url,
            args.embedding_model_name,
            args.top_n,
        ]
    ):
        result = update_config(
            api_key=args.api_key,
            model_name=args.model_name,
            base_url=args.base_url,
            embedding_model_name=args.embedding_model_name,
            top_n=args.top_n,
        )
    else:
        result = get_config()
    _output(result)


def _cmd_material_add_dir(args: argparse.Namespace) -> None:
    """添加素材目录并向量化入库。"""
    from material import add_directory

    result = add_directory(args.directory)
    _output(result, 0 if result.get("status") == "ok" else 2)


def _cmd_material_sync(args: argparse.Namespace) -> None:
    """同步素材库（新增/删除文件）。"""
    from material import sync_materials

    result = sync_materials()
    _output(result, 0 if result.get("status") == "ok" else 2)


def _cmd_material_search(args: argparse.Namespace) -> None:
    """根据文本搜索匹配素材。"""
    from material import search_materials

    result = search_materials(
        query=args.query,
        media_type=args.media_type,
        top_n=args.top_n,
    )
    _output(result)


def _cmd_material_list(args: argparse.Namespace) -> None:
    """列出所有已入库素材。"""
    from material import list_materials

    result = list_materials(media_type=args.media_type)
    _output(result)


def _cmd_material_stats(args: argparse.Namespace) -> None:
    """查看素材库统计信息。"""
    from material import get_stats

    result = get_stats()
    _output(result)


def _cmd_material_remove_dir(args: argparse.Namespace) -> None:
    """移除素材目录。"""
    from material import remove_directory

    result = remove_directory(args.directory, keep_db=args.keep_db)
    _output(result, 0 if result.get("status") == "ok" else 2)


def main() -> None:
    parser = argparse.ArgumentParser(description="抖音自动化 CLI")
    parser.add_argument("--host", default="127.0.0.1", help="Chrome DevTools 主机")
    parser.add_argument("--port", type=int, default=9222, help="Chrome DevTools 端口")
    parser.add_argument("--account", default="", help="账号名称（多账号隔离）")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # === 认证 ===
    p_check_login = subparsers.add_parser("check-login", help="检查登录状态")
    p_check_login.set_defaults(func=_cmd_check_login)

    # === 登录 ===
    p_login = subparsers.add_parser(
        "login",
        help="获取二维码或一键登录，输出后退出，保持页面供后续 check-scan-status",
    )
    p_login.add_argument(
        "--switch-account",
        action="store_true",
        help="切换账号模式：若出现一键登录，会点击「登录其他账号」切换到二维码",
    )
    p_login.set_defaults(func=_cmd_login)

    p_check_scan = subparsers.add_parser(
        "check-scan-status",
        help="检查扫码后的页面状态，用户告知已完成扫码后执行；若需身份验证会触发验证码",
    )
    p_check_scan.set_defaults(func=_cmd_check_scan_status)

    # === 手机验证码登录 ===
    p_send_code = subparsers.add_parser(
        "send-code", help="分步登录第一步：发送手机验证码，保持页面不关闭"
    )
    p_send_code.add_argument("--phone", required=True, help="手机号（不含国家码）")
    p_send_code.set_defaults(func=_cmd_send_code)

    # === 验证码登录 ===
    p_verify_code = subparsers.add_parser(
        "verify-code", help="分步登录第二步：填写验证码并完成登录"
    )
    p_verify_code.add_argument("--code", required=True, help="收到的短信验证码")
    p_verify_code.set_defaults(func=_cmd_verify_code)

    # === 退出登录 ===
    p_logout = subparsers.add_parser("logout", help="退出登录")
    p_logout.set_defaults(func=_cmd_logout)

    # === 关闭浏览器 ===
    p_close_browser = subparsers.add_parser(
        "close-browser", help="关闭浏览器 tab，完成请求后收尾"
    )
    p_close_browser.set_defaults(func=_cmd_close_browser)

    p_chrome = subparsers.add_parser("chrome", help="Chrome 进程管理")
    p_chrome.add_argument(
        "chrome_action",
        choices=["start", "stop", "status", "restart"],
        help="start=启动, stop=关闭, status=状态, restart=重启",
    )
    p_chrome.set_defaults(func=_cmd_chrome)

    # === 热榜 ===
    p_hot_list = subparsers.add_parser(
        "get-hot-list",
        help="获取抖音实时热榜（需浏览器连接）",
    )
    p_hot_list.add_argument(
        "--count", type=int, default=50, help="获取数量（默认 50）"
    )
    p_hot_list.add_argument(
        "--no-save",
        action="store_true",
        help="不保存到数据库（默认会保存）",
    )
    p_hot_list.set_defaults(func=_cmd_get_hot_list)

    p_query_hot = subparsers.add_parser(
        "query-hot-list",
        help="查询本地缓存热榜（无需浏览器）",
    )
    p_query_hot.add_argument(
        "--date", default="", help="日期 YYYY-MM-DD（默认查最新一次）"
    )
    p_query_hot.add_argument(
        "--limit", type=int, default=50, help="数量限制（默认 50）"
    )
    p_query_hot.set_defaults(func=_cmd_query_hot_list)

    p_hot_stats = subparsers.add_parser(
        "hot-list-stats",
        help="热榜统计信息（无需浏览器）",
    )
    p_hot_stats.add_argument(
        "--days", type=int, default=7, help="统计天数（默认 7）"
    )
    p_hot_stats.set_defaults(func=_cmd_hot_list_stats)

    p_hot_logs = subparsers.add_parser(
        "hot-list-logs",
        help="热榜抓取日志（无需浏览器）",
    )
    p_hot_logs.add_argument(
        "--limit", type=int, default=10, help="数量限制（默认 10）"
    )
    p_hot_logs.set_defaults(func=_cmd_hot_list_logs)

    # === 浏览 ===
    p_search = subparsers.add_parser(
        "search-videos",
        help="搜索视频（仅返回列表；需详情时调用 get-video-detail）",
    )
    p_search.add_argument("--keyword", required=True, help="搜索关键词")
    p_search.add_argument(
        "--count",
        type=int,
        default=20,
        help="获取数量（默认 20）",
    )
    p_search.add_argument("--sort-by", default="", help="排序方式")
    p_search.set_defaults(func=_cmd_search_videos)

    p_detect_type = subparsers.add_parser(
        "detect-type",
        help="探测 aweme 类型（视频/笔记），通过 API 查询，不导航页面",
    )
    p_detect_type.add_argument("--aweme-id", required=True, help="作品 ID（aweme_id）")
    p_detect_type.set_defaults(func=_cmd_detect_type)

    p_video_detail = subparsers.add_parser(
        "get-video-detail",
        help="获取视频/笔记详情（视频含字幕、可选转录；笔记含图片列表）；批量获取时建议每次间隔 3.0～5.0 秒",
    )
    p_video_detail.add_argument("--video-id", required=True, help="视频/笔记 ID（aweme_id）")
    p_video_detail.add_argument(
        "--aweme-type",
        default="video",
        choices=["video", "note"],
        help=(
            "作品类型：video（普通视频，默认）或 note（图文笔记，aweme_type=68）。"
            "传 note 时使用 /note/ URL 并跳过字幕和转录，返回图片列表。"
            "不传时 CLI 会从 API 返回的 aweme_type 自动识别。"
            "识别信号：链接含 /note/、search-videos 返回 aweme_type=68、或用户明确说'笔记'。"
        ),
    )
    p_video_detail.add_argument(
        "--max-comments", type=int, default=30, help="最大评论数"
    )
    p_video_detail.add_argument(
        "--no-fetch-subtitles",
        action="store_true",
        help="跳过字幕获取（默认会尝试获取字幕，补充列表页无的 subtitle_text）",
    )
    p_video_detail.add_argument(
        "--no-fetch-transcript",
        action="store_true",
        help="跳过 Whisper 转录（默认会转录；>16s 跳过避免超时）",
    )
    p_video_detail.add_argument(
        "--whisper-model",
        default="base",
        help="Whisper 模型（base/tiny/small/medium/large），默认 base 以提升中文准确率",
    )
    p_video_detail.add_argument(
        "--output-dir",
        default="",
        help="字幕/转录输出目录（默认 ~/.dingclaw/store-douyin/downloads/）",
    )
    p_video_detail.set_defaults(func=_cmd_get_video_detail)

    p_download = subparsers.add_parser("download-video", help="下载视频")
    p_download.add_argument("--video-id", required=True, help="视频 ID")
    p_download.add_argument(
        "--output-dir",
        default="",
        help="输出目录（默认 ~/.dingclaw/store-douyin/downloads/）",
    )
    p_download.add_argument(
        "--filename", default="", help="文件名（不含扩展名，默认使用视频 ID）"
    )
    p_download.set_defaults(func=_cmd_download_video)

    p_subtitles = subparsers.add_parser("extract-subtitles", help="提取视频字幕")
    p_subtitles.add_argument("--video-id", required=True, help="视频 ID")
    p_subtitles.add_argument(
        "--output-dir",
        default="",
        help="输出目录（默认 ~/.dingclaw/store-douyin/downloads/）",
    )
    p_subtitles.set_defaults(func=_cmd_extract_subtitles)

    p_user_profile = subparsers.add_parser(
        "user-profile",
        help="获取用户主页（默认对前 N 条视频补充详情，含 get-video-detail 逻辑：点赞、评论、封面、字幕）",
    )
    p_user_profile.add_argument("--user-id", required=True, help="用户 sec_uid")
    p_user_profile.add_argument(
        "--max-videos",
        type=int,
        default=10,
        help="最大视频数（默认 10，含详情时易超时；需更多时建议 --no-enrich-details）",
    )
    p_user_profile.add_argument(
        "--no-enrich-details",
        action="store_true",
        help="不获取视频详情，仅返回列表",
    )
    p_user_profile.add_argument(
        "--no-fetch-subtitles",
        action="store_true",
        help="补充详情时跳过字幕获取（默认会尝试获取字幕）",
    )
    p_user_profile.add_argument(
        "--no-fetch-transcript",
        action="store_true",
        help="补充详情时跳过 Whisper 转录（默认会转录；>16s 跳过避免超时）",
    )
    p_user_profile.add_argument(
        "--max-enrich",
        type=int,
        default=5,
        help="最多对前 N 条补充详情（默认 5，避免超时；列表仍返回 max-videos 条）",
    )
    p_user_profile.add_argument(
        "--output-dir",
        default="",
        help="字幕/转录输出目录（默认 ~/.dingclaw/store-douyin/downloads/）",
    )
    p_user_profile.set_defaults(func=_cmd_user_profile)

    p_my_profile = subparsers.add_parser(
        "my-profile",
        help="获取当前登录账号主页（默认对前 N 条视频补充详情，含 get-video-detail 逻辑）",
    )
    p_my_profile.add_argument(
        "--max-videos",
        type=int,
        default=10,
        help="最大视频数（默认 10，含详情时易超时；需更多时建议 --no-enrich-details）",
    )
    p_my_profile.add_argument(
        "--no-enrich-details",
        action="store_true",
        help="不获取视频详情，仅返回列表",
    )
    p_my_profile.add_argument(
        "--no-fetch-subtitles",
        action="store_true",
        help="补充详情时跳过字幕获取",
    )
    p_my_profile.add_argument(
        "--no-fetch-transcript",
        action="store_true",
        help="补充详情时跳过 Whisper 转录（默认会转录；>16s 跳过避免超时）",
    )
    p_my_profile.add_argument(
        "--max-enrich",
        type=int,
        default=10,
        help="最多对前 N 条补充详情（默认 5，避免超时；列表仍返回 max-videos 条）",
    )
    p_my_profile.add_argument(
        "--output-dir",
        default="",
        help="字幕/转录输出目录",
    )
    p_my_profile.set_defaults(func=_cmd_my_profile)

    # === 互动 ===
    p_post_comment = subparsers.add_parser("post-comment", help="发表评论（视频或笔记）")
    p_post_comment.add_argument("--video-id", help="视频 ID（与 --note-id 二选一）")
    p_post_comment.add_argument("--note-id", help="笔记 ID（与 --video-id 二选一）")
    p_post_comment.add_argument("--content", required=True, help="评论内容")
    p_post_comment.set_defaults(func=_cmd_post_comment)

    p_reply_comment = subparsers.add_parser("reply-comment", help="回复评论（视频或笔记）")
    p_reply_comment.add_argument("--video-id", help="视频 ID（与 --note-id 二选一）")
    p_reply_comment.add_argument("--note-id", help="笔记 ID（与 --video-id 二选一）")
    p_reply_comment.add_argument("--comment-id", required=True, help="评论 ID")
    p_reply_comment.add_argument("--content", required=True, help="回复内容")
    p_reply_comment.set_defaults(func=_cmd_reply_comment)

    p_like = subparsers.add_parser("like-video", help="点赞视频")
    p_like.add_argument("--video-id", required=True, help="视频 ID")
    p_like.add_argument("--unlike", action="store_true", help="取消点赞")
    p_like.set_defaults(func=_cmd_like_video)

    p_favorite = subparsers.add_parser("favorite-video", help="收藏视频")
    p_favorite.add_argument("--video-id", required=True, help="视频 ID")
    p_favorite.add_argument("--unfavorite", action="store_true", help="取消收藏")
    p_favorite.set_defaults(func=_cmd_favorite_video)

    # === 本地查询（无需浏览器）===
    p_query_videos = subparsers.add_parser("query-videos", help="查询本地缓存视频")
    p_query_videos.add_argument("--mine", action="store_true", help="仅查我的视频")
    p_query_videos.add_argument("--keyword", default="", help="关键词过滤")
    p_query_videos.add_argument("--limit", type=int, default=20, help="数量限制")
    p_query_videos.add_argument("--offset", type=int, default=0, help="偏移量")
    p_query_videos.set_defaults(func=_cmd_query_videos)

    p_query_comments = subparsers.add_parser("query-comments", help="查询本地缓存评论")
    p_query_comments.add_argument("--video-id", default="", help="所属视频 ID")
    p_query_comments.add_argument("--mine", action="store_true", help="仅查我的评论")
    p_query_comments.add_argument("--limit", type=int, default=20, help="数量限制")
    p_query_comments.set_defaults(func=_cmd_query_comments)

    p_search_local = subparsers.add_parser("search-local", help="本地全文 LIKE 检索")
    p_search_local.add_argument("--query", required=True, help="检索关键词")
    p_search_local.add_argument(
        "--target", default="videos", choices=["videos", "comments"], help="检索目标"
    )
    p_search_local.add_argument("--limit", type=int, default=10, help="数量限制")
    p_search_local.set_defaults(func=_cmd_search_local)

    p_trend = subparsers.add_parser("trend-analysis", help="关键词互动趋势分析")
    p_trend.add_argument("--keyword", required=True, help="关键词")
    p_trend.add_argument("--days", type=int, default=30, help="分析天数")
    p_trend.set_defaults(func=_cmd_trend_analysis)

    # === 发布 ===

    # 视频格式检测
    p_check_fmt = subparsers.add_parser(
        "check-video-format", help="检测视频格式（大小/时长/分辨率）"
    )
    p_check_fmt.add_argument("--video-file", required=True, help="视频文件路径")
    p_check_fmt.add_argument(
        "--type", default="normal", choices=["normal", "vr"], help="视频类型"
    )
    p_check_fmt.set_defaults(func=_cmd_check_video_format)

    # 填写普通视频表单
    p_fill_video = subparsers.add_parser(
        "fill-publish-video", help="填写普通视频发布表单（不点击发布）"
    )
    p_fill_video.add_argument(
        "--video-file", required=True, help="视频文件路径（绝对路径）"
    )
    p_fill_video.add_argument("--title", default="", help="作品标题")
    p_fill_video.add_argument("--title-file", default="", help="标题文件路径")
    p_fill_video.add_argument("--content", default="", help="作品简介")
    p_fill_video.add_argument("--content-file", default="", help="简介文件路径")
    p_fill_video.add_argument("--location", default="", help="位置标签")
    p_fill_video.add_argument("--product", default="", help="同款好物标签（标记好物）")
    p_fill_video.add_argument("--hotspot", default="", help="关联热点词")
    p_fill_video.add_argument(
        "--visibility",
        default="公开",
        choices=["公开", "好友可见", "仅自己可见"],
        help="可见范围",
    )
    p_fill_video.add_argument(
        "--allow-save",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="允许保存（--no-allow-save 表示不允许）",
    )
    p_fill_video.set_defaults(func=_cmd_fill_publish_video)

    # 填写全景视频表单
    p_fill_vr = subparsers.add_parser(
        "fill-publish-vr", help="填写全景视频发布表单（不点击发布）"
    )
    p_fill_vr.add_argument(
        "--video-file", required=True, help="全景视频文件路径（绝对路径）"
    )
    p_fill_vr.add_argument("--title", default="", help="作品标题")
    p_fill_vr.add_argument("--title-file", default="", help="标题文件路径")
    p_fill_vr.add_argument("--content", default="", help="作品简介")
    p_fill_vr.add_argument("--content-file", default="", help="简介文件路径")
    p_fill_vr.add_argument(
        "--vr-format",
        default="普通360°全景视频",
        choices=[
            "普通360°全景视频",
            "立体360°全景视频",
            "普通180°视频",
            "立体180°视频",
        ],
        help="全景视频格式",
    )
    p_fill_vr.add_argument(
        "--cover", default="", help="封面图片路径（留空使用官方生成）"
    )
    p_fill_vr.add_argument("--location", default="", help="位置标签")
    p_fill_vr.add_argument("--product", default="", help="同款好物标签（标记好物）")
    p_fill_vr.add_argument("--hotspot", default="", help="关联热点词")
    p_fill_vr.add_argument(
        "--visibility",
        default="公开",
        choices=["公开", "好友可见", "仅自己可见"],
        help="可见范围",
    )
    p_fill_vr.add_argument(
        "--allow-save",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="允许保存（--no-allow-save 表示不允许）",
    )
    p_fill_vr.set_defaults(func=_cmd_fill_publish_vr)

    # 填写图文表单
    p_fill_image = subparsers.add_parser(
        "fill-publish-image", help="填写图文发布表单（不点击发布）"
    )
    p_fill_image.add_argument(
        "--images", nargs="+", required=True, help="图片路径列表（绝对路径，最多35张）"
    )
    p_fill_image.add_argument("--title", default="", help="作品标题")
    p_fill_image.add_argument("--title-file", default="", help="标题文件路径")
    p_fill_image.add_argument("--content", default="", help="作品描述")
    p_fill_image.add_argument("--content-file", default="", help="描述文件路径")
    p_fill_image.add_argument(
        "--topics", nargs="*", default=[], help="话题标签列表，自动追加到描述末尾"
    )
    p_fill_image.add_argument("--music", default="", help="背景音乐名称（留空不选择）")
    p_fill_image.add_argument("--location", default="", help="位置标签")
    p_fill_image.add_argument("--product", default="", help="同款好物标签（标记好物）")
    p_fill_image.add_argument("--hotspot", default="", help="关联热点词")
    p_fill_image.add_argument(
        "--visibility",
        default="公开",
        choices=["公开", "好友可见", "仅自己可见"],
        help="可见范围",
    )
    p_fill_image.add_argument(
        "--allow-save",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="允许保存（--no-allow-save 表示不允许）",
    )
    p_fill_image.set_defaults(func=_cmd_fill_publish_image)

    # 填写文章表单
    p_fill_article = subparsers.add_parser(
        "fill-publish-article", help="填写文章发布表单（不点击发布）"
    )
    p_fill_article.add_argument("--title", default="", help="文章标题")
    p_fill_article.add_argument("--title-file", default="", help="标题文件路径")
    p_fill_article.add_argument(
        "--content", default="", help="文章正文（Markdown 格式）"
    )
    p_fill_article.add_argument("--content-file", default="", help="正文文件路径")
    p_fill_article.add_argument("--summary", default="", help="文章摘要")
    p_fill_article.add_argument("--summary-file", default="", help="摘要文件路径")
    p_fill_article.add_argument(
        "--cover", default="", help="封面图片路径（绝对路径，留空跳过封面上传）"
    )
    p_fill_article.add_argument(
        "--topics", nargs="*", default=[], help="话题列表（最多5个）"
    )
    p_fill_article.add_argument(
        "--music", default="", help="背景音乐名称（留空不选择）"
    )
    p_fill_article.add_argument(
        "--visibility",
        default="公开",
        choices=["公开", "好友可见", "仅自己可见"],
        help="可见范围",
    )
    p_fill_article.set_defaults(func=_cmd_fill_publish_article)

    # 点击发布
    p_click_publish = subparsers.add_parser(
        "click-publish", help="点击发布按钮，监听发布接口返回结果"
    )
    p_click_publish.set_defaults(func=_cmd_click_publish)

    # 普通视频一步到位发布
    p_publish_video = subparsers.add_parser(
        "publish-video", help="普通视频一步到位发布"
    )
    p_publish_video.add_argument(
        "--video-file", required=True, help="视频文件路径（绝对路径）"
    )
    p_publish_video.add_argument("--title", default="", help="作品标题")
    p_publish_video.add_argument("--title-file", default="", help="标题文件路径")
    p_publish_video.add_argument("--content", default="", help="作品简介")
    p_publish_video.add_argument("--content-file", default="", help="简介文件路径")
    p_publish_video.add_argument("--location", default="", help="位置标签")
    p_publish_video.add_argument(
        "--product", default="", help="同款好物标签（标记好物）"
    )
    p_publish_video.add_argument("--hotspot", default="", help="关联热点词")
    p_publish_video.add_argument(
        "--visibility",
        default="公开",
        choices=["公开", "好友可见", "仅自己可见"],
        help="可见范围",
    )
    p_publish_video.add_argument(
        "--allow-save",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="允许保存（--no-allow-save 表示不允许）",
    )
    p_publish_video.set_defaults(func=_cmd_publish_video)

    # === 素材管理 ===

    p_mat_check = subparsers.add_parser(
        "material-check", help="检查素材管理依赖是否安装"
    )
    p_mat_check.set_defaults(func=_cmd_material_check)

    p_mat_dl_model = subparsers.add_parser(
        "material-download-model", help="下载本地 Embedding 模型"
    )
    p_mat_dl_model.set_defaults(func=_cmd_material_download_model)

    p_mat_config = subparsers.add_parser(
        "material-config", help="查看或修改素材管理配置"
    )
    p_mat_config.add_argument("--api-key", default="", help="大模型 API Key")
    p_mat_config.add_argument(
        "--model-name", default="", help="多模态大模型名称（如 qwen3-vl-plus）"
    )
    p_mat_config.add_argument("--base-url", default="", help="大模型 API Base URL")
    p_mat_config.add_argument(
        "--embedding-model-name", default="", help="Embedding 模型名称（远程回退用）"
    )
    p_mat_config.add_argument(
        "--top-n", type=int, default=0, help="搜索返回数量（0 表示不修改）"
    )
    p_mat_config.set_defaults(func=_cmd_material_config)

    p_mat_add_dir = subparsers.add_parser(
        "material-add-dir", help="添加素材目录并向量化入库"
    )
    p_mat_add_dir.add_argument("--directory", required=True, help="素材目录绝对路径")
    p_mat_add_dir.set_defaults(func=_cmd_material_add_dir)

    p_mat_sync = subparsers.add_parser(
        "material-sync", help="同步素材库（新增/删除文件）"
    )
    p_mat_sync.set_defaults(func=_cmd_material_sync)

    p_mat_search = subparsers.add_parser("material-search", help="根据文本搜索匹配素材")
    p_mat_search.add_argument("--query", required=True, help="搜索文本")
    p_mat_search.add_argument(
        "--media-type", default="", choices=["", "image", "video"], help="素材类型过滤"
    )
    p_mat_search.add_argument(
        "--top-n", type=int, default=0, help="返回数量（0 使用配置默认值）"
    )
    p_mat_search.set_defaults(func=_cmd_material_search)

    p_mat_list = subparsers.add_parser("material-list", help="列出所有已入库素材")
    p_mat_list.add_argument(
        "--media-type", default="", choices=["", "image", "video"], help="素材类型过滤"
    )
    p_mat_list.set_defaults(func=_cmd_material_list)

    p_mat_stats = subparsers.add_parser("material-stats", help="查看素材库统计信息")
    p_mat_stats.set_defaults(func=_cmd_material_stats)

    p_mat_rm_dir = subparsers.add_parser("material-remove-dir", help="移除素材目录")
    p_mat_rm_dir.add_argument(
        "--directory", required=True, help="要移除的素材目录绝对路径"
    )
    p_mat_rm_dir.add_argument(
        "--keep-db", action="store_true", help="移除目录但保留数据库记录"
    )
    p_mat_rm_dir.set_defaults(func=_cmd_material_remove_dir)

    args = parser.parse_args()

    try:
        args.func(args)
    except Exception as e:
        logger.error("执行失败: %s", e, exc_info=True)
        _output({"success": False, "error": str(e)}, 2)


if __name__ == "__main__":
    main()
