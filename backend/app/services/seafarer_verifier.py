"""海员证网站核验服务。

该模块把原本独立的 Playwright 查询程序改造成当前船代系统可调用的后台任务：
每个航次一次只运行一个核验任务，完成一名船员后立即写入数据库并更新任务进度。
"""

import asyncio
import json
import os
import threading
import uuid
from datetime import datetime
from typing import Any, Callable

from sqlalchemy import select

from ..db import SessionLocal
from ..models import CrewMember, SeafarerVerification


DEFAULT_QUERY_URL = "https://cyxx.msa.gov.cn/crew_qey/qry/certInit.action"
_jobs: dict[int, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


class QueryCancelled(Exception):
    """用户主动停止了当前核验任务。"""


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _extra(member: CrewMember) -> dict[str, Any]:
    try:
        value = json.loads(member.extra_json or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def is_chinese_nationality(value: str | None) -> bool:
    raw = str(value or "").strip().lower()
    normalized = raw.replace("-", "").replace(" ", "")
    return any(token in normalized for token in ("cn", "chn", "中国", "china"))


def is_seafarer_certificate(member: CrewMember) -> bool:
    document_type = str(_extra(member).get("document_type") or "").strip().lower()
    if not document_type:
        return False
    return any(token in document_type for token in ("海员证", "seaman", "seafarer"))


def eligibility(member: CrewMember) -> tuple[bool, str]:
    if not is_chinese_nationality(member.nationality):
        return False, "非中国籍"
    if not is_seafarer_certificate(member):
        return False, "证件类别不是海员证或未填写"
    if not (member.document_no or "").strip():
        return False, "证件号码为空"
    return True, ""


def crew_verification_row(member: CrewMember, verification: SeafarerVerification | None = None) -> dict[str, Any]:
    eligible, reason = eligibility(member)
    extra = _extra(member)
    return {
        "id": verification.id if verification else None,
        "crew_member_id": member.id,
        "name": member.name,
        "nationality": member.nationality,
        "rank": member.rank,
        "document_no": member.document_no,
        "document_type": extra.get("document_type"),
        "eligible": eligible,
        "ineligible_reason": reason,
        "status": verification.status if verification else ("待查询" if eligible else "不适用"),
        "website_certificate_no": verification.website_certificate_no if verification else None,
        "website_name": verification.website_name if verification else None,
        "certificate_status": verification.certificate_status if verification else None,
        "issuing_authority": verification.issuing_authority if verification else None,
        "issue_date": verification.issue_date if verification else None,
        "valid_date": verification.valid_date if verification else None,
        "error_info": verification.error_info if verification else None,
        "attempts": verification.attempts if verification else 0,
        "queried_at": verification.queried_at.isoformat() if verification and verification.queried_at else None,
    }


class CaptchaSolver:
    """使用 ddddocr 的两个模型交叉确认四位验证码。"""

    def __init__(self):
        import ddddocr

        self._old = ddddocr.DdddOcr(old=True)
        self._new = ddddocr.DdddOcr(old=False)

    def solve_checked(self, image_bytes: bytes, expected_len: int = 4) -> tuple[str, bool]:
        first = second = ""
        try:
            first = (self._old.classification(image_bytes) or "").strip()
        except Exception:
            pass
        try:
            second = (self._new.classification(image_bytes) or "").strip()
        except Exception:
            pass
        return first, bool(first) and len(first) == expected_len and first.lower() == second.lower()


class SeafarerQueryRunner:
    """单线程、低频率查询海员证网站。"""

    def __init__(self, on_log: Callable[[str], None] | None = None, stop_event: threading.Event | None = None):
        self.on_log = on_log or (lambda _message: None)
        self.stop_event = stop_event or threading.Event()
        self.query_url = os.getenv("SHIP_AGENCY_SEAFARER_QUERY_URL", DEFAULT_QUERY_URL)
        self.timeout_ms = int(os.getenv("SHIP_AGENCY_SEAFARER_TIMEOUT_MS", "30000"))
        self.retry_count = max(1, int(os.getenv("SHIP_AGENCY_SEAFARER_RETRY_COUNT", "3")))
        self.interval_seconds = max(0.0, float(os.getenv("SHIP_AGENCY_SEAFARER_INTERVAL_SECONDS", "3")))
        self.rate_limit_wait_seconds = max(1.0, float(os.getenv("SHIP_AGENCY_SEAFARER_RATE_LIMIT_WAIT_SECONDS", "60")))
        # 云服务器通常没有桌面显示环境，Linux 下必须使用无头模式；
        # Windows 本地仍保留可见浏览器，便于调试和人工观察验证码页面。
        default_headless = os.name != "nt"
        self.headless = _as_bool(os.getenv("SHIP_AGENCY_SEAFARER_HEADLESS"), default_headless)
        self._pw = self._browser = self._context = self._page = None

    def run(self, crew_rows: list[dict[str, Any]], on_result: Callable[[dict[str, Any]], None]) -> None:
        asyncio.run(self._run(crew_rows, on_result))

    async def _run(self, crew_rows: list[dict[str, Any]], on_result: Callable[[dict[str, Any]], None]):
        try:
            from playwright.async_api import async_playwright

            self._captcha = CaptchaSolver()
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(headless=self.headless)
            self._context = await self._browser.new_context(
                viewport={"width": 1280, "height": 800}, locale="zh-CN"
            )
            self._page = await self._context.new_page()
            for index, crew in enumerate(crew_rows):
                self._check_cancelled()
                result = await self._query_one(crew)
                if not self.stop_event.is_set():
                    on_result(result)
                if index < len(crew_rows) - 1 and self.interval_seconds:
                    await self._sleep_or_cancel(self.interval_seconds)
        finally:
            await self._close()

    async def _close(self):
        for resource in (self._context, self._browser):
            if resource:
                try:
                    await resource.close()
                except Exception:
                    pass
        if self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass
        self._pw = self._browser = self._context = self._page = None

    async def _query_one(self, crew: dict[str, Any]) -> dict[str, Any]:
        base = {
            "crew_member_id": crew["crew_member_id"],
            "status": "待查询",
            "website_certificate_no": None,
            "website_name": None,
            "certificate_status": None,
            "issuing_authority": None,
            "issue_date": None,
            "valid_date": None,
            "error_info": None,
            "attempts": 0,
        }
        cert_no = str(crew.get("document_no") or "").strip()
        if not cert_no:
            base.update(status="无效", error_info="证件号码为空")
            return base

        for attempt in range(self.retry_count):
            self._check_cancelled()
            base["attempts"] = attempt + 1
            if attempt:
                base["status"] = "重试中"
            try:
                await self._navigate()
                self._check_cancelled()
                await self._fill_form(cert_no)
                code = await self._get_captcha()
                self._check_cancelled()
                if not code:
                    base.update(status="重试中", error_info="验证码识别失败")
                    continue
                await self._page.locator("#yanzhm").first.fill(code)
                await self._click_search()
                await self._sleep_or_cancel(3)
                body = await self._page.inner_text("body")

                if self._rate_limited(body):
                    base.update(status="重试中", error_info="访问频率过高")
                    self._log(f"{crew.get('name', '')}：访问频率受限，等待{int(self.rate_limit_wait_seconds)}秒")
                    await self._sleep_or_cancel(self.rate_limit_wait_seconds)
                    continue
                if "验证码错误" in body or "验证码不正确" in body:
                    base.update(status="重试中", error_info="验证码错误")
                    continue
                if "查询成功" in body:
                    parsed = await self._parse(body)
                    if parsed:
                        base.update(parsed)
                        base["status"] = "有效" if parsed.get("certificate_status") == "有效" else "无效"
                        # 成功后清除前几次尝试遗留的验证码/限流错误。
                        base["error_info"] = None
                        return base
                    base.update(status="重试中", error_info="查询成功但结果解析失败")
                    continue
                if any(text in body for text in ("没有查询到", "无此证书", "未查询到")):
                    base.update(status="无效", error_info="无符合条件的证书信息")
                    return base
                base.update(status="重试中", error_info="查询未执行")
            except Exception as exc:
                base.update(status="重试中", error_info=str(exc)[:256])
                self._log(f"{crew.get('name', '')}：{str(exc)[:120]}")
                if attempt < self.retry_count - 1:
                    await self._sleep_or_cancel(2)

        if base["status"] == "重试中":
            base["error_info"] = base["error_info"] or f"重试{self.retry_count}次仍未完成"
        return base

    async def _navigate(self):
        self._check_cancelled()
        try:
            await self._page.goto(self.query_url, wait_until="networkidle", timeout=self.timeout_ms)
        except Exception:
            await self._page.goto(self.query_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            await self._sleep_or_cancel(2)

    async def _fill_form(self, cert_no: str):
        await self._page.locator("input[name='lycxQO.applScope'][value='Z']").first.check()
        await self._page.locator("input[name='lycxQO.zslxarr'][value='Y']").first.check()
        await self._page.locator("#zshm").first.fill(cert_no)

    async def _get_captcha(self, max_tries: int = 5) -> str:
        image = self._page.locator("img[src*='getValidateImage'], img#yanzhmpic")
        if await image.count() == 0:
            return ""
        for _ in range(max_tries):
            try:
                image_bytes = await image.first.screenshot(type="png")
                code, ok = self._captcha.solve_checked(image_bytes)
                if ok:
                    return code
                await image.first.click()
            except Exception:
                pass
            await asyncio.sleep(0.6)
        return ""

    async def _click_search(self):
        selectors = (
            "input[value='查询']",
            "input[value='查 询']",
            "button:has-text('查询')",
            "a:has-text('查询')",
            "input[value*='查询']",
            "input[value*='查']",
            "button:has-text('查')",
            "a:has-text('查')",
            "input[onclick*='query']",
            "input[onclick*='Query']",
            "input[onclick*='qry']",
            "input[onclick*='Qry']",
            "input[onclick*='submit']",
            "button[onclick*='query']",
            "button[onclick*='Query']",
            "button[onclick*='qry']",
            "button[onclick*='Qry']",
            "button[onclick*='submit']",
            "a[onclick*='query']",
            "a[onclick*='Query']",
            "a[onclick*='qry']",
            "a[onclick*='Qry']",
            "a[onclick*='submit']",
            "img[onclick*='query']",
            "img[onclick*='Query']",
            "img[onclick*='qry']",
            "img[onclick*='Qry']",
            "img[onclick*='submit']",
            "input[type='button']",
            "input[type='submit']",
            "input[type='image']",
        )
        for selector in selectors:
            locator = self._page.locator(selector)
            for index in range(await locator.count()):
                item = locator.nth(index)
                if await item.is_visible():
                    await item.click()
                    return

        # 页面版本变化时，按属性和可见文字扫描可能的提交控件。
        candidates = self._page.locator(
            "input,button,a,img,[onclick]"
        )
        query_words = ("查询", "查 询", "query", "qry", "search", "submit", "doquery", "check")
        for index in range(await candidates.count()):
            item = candidates.nth(index)
            try:
                if not await item.is_visible():
                    continue
                attrs = await item.evaluate("""el => ({
                    text: (el.innerText || '').trim(),
                    value: el.getAttribute('value') || '',
                    id: el.id || '',
                    title: el.getAttribute('title') || '',
                    onclick: el.getAttribute('onclick') || ''
                })""")
                haystack = " ".join(str(attrs.get(key) or "") for key in ("text", "value", "id", "title", "onclick")).lower()
                if any(word.lower() in haystack for word in query_words):
                    await item.click()
                    return
            except Exception:
                continue

        # 最后尝试提交验证码所在表单；部分旧页面没有可识别的按钮文本。
        captcha = self._page.locator("#yanzhm").first
        if await captcha.count() and await captcha.is_visible():
            await captcha.press("Enter")
            return
        forms = self._page.locator("form")
        for index in range(await forms.count()):
            form = forms.nth(index)
            if await form.is_visible():
                await form.press("Enter")
                return
        raise RuntimeError("未找到海员证查询提交按钮")

    async def _parse(self, body: str) -> dict[str, Any] | None:
        result: dict[str, str] = {}
        for table in await self._page.query_selector_all("table"):
            headers = None
            for row in await table.query_selector_all("tr"):
                ths = await row.query_selector_all("th")
                tds = await row.query_selector_all("td")
                if ths:
                    headers = [(await item.inner_text()).strip() for item in ths]
                elif tds and headers:
                    values = [(await item.inner_text()).strip() for item in tds]
                    for key, value in zip(headers, values):
                        if key and value:
                            result.setdefault(key, value)
                    break
        if not result:
            for table in await self._page.query_selector_all("table"):
                for row in await table.query_selector_all("tr"):
                    cells = await row.query_selector_all("td, th")
                    if len(cells) >= 2:
                        key = (await cells[0].inner_text()).strip().rstrip("：:")
                        value = (await cells[1].inner_text()).strip()
                        if key and value:
                            result[key] = value

        mapping = {
            "证书号码": "website_certificate_no", "海员证号码": "website_certificate_no", "证书编号": "website_certificate_no",
            "姓名": "website_name", "证书状态": "certificate_status", "状态": "certificate_status",
            "签发机关": "issuing_authority", "签发日期": "issue_date", "有效日期": "valid_date", "有效期至": "valid_date",
        }
        parsed: dict[str, Any] = {}
        for raw_key, value in result.items():
            for pattern, key in mapping.items():
                if pattern in raw_key:
                    parsed[key] = value
        if not parsed:
            for line in body.splitlines():
                line = line.strip()
                for pattern, key in mapping.items():
                    if pattern in line and key not in parsed:
                        value = line.split(pattern, 1)[-1].lstrip("：:").strip()
                        if value and len(value) < 100:
                            parsed[key] = value
        return parsed or None

    @staticmethod
    def _rate_limited(body: str) -> bool:
        return any(keyword in body for keyword in ("访问频率", "频率过高", "过于频繁", "操作频繁", "访问过于频繁", "请稍后再试"))

    def _log(self, message: str):
        self.on_log(f"[{datetime.now():%H:%M:%S}] {message}")

    def _check_cancelled(self):
        if self.stop_event.is_set():
            raise QueryCancelled("用户已停止海员证核验")

    async def _sleep_or_cancel(self, seconds: float):
        deadline = asyncio.get_running_loop().time() + max(0.0, seconds)
        while True:
            self._check_cancelled()
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return
            await asyncio.sleep(min(0.25, remaining))


def _job_snapshot(job: dict[str, Any]) -> dict[str, Any]:
    with _jobs_lock:
        return {
            "job_id": job["job_id"],
            "status": job["status"],
            "processed": job["processed"],
            "total": job["total"],
            "error": job.get("error"),
            "items": list(job["items"].values()),
        }


def current_job(voyage_id: int) -> dict[str, Any] | None:
    with _jobs_lock:
        job = _jobs.get(voyage_id)
    return _job_snapshot(job) if job else None


def start_job(voyage_id: int, rows: list[dict[str, Any]], on_log: Callable[[str], None] | None = None) -> dict[str, Any]:
    with _jobs_lock:
        existing = _jobs.get(voyage_id)
        if not (existing and existing["status"] in {"排队中", "查询中"}):
            job = {
                "job_id": uuid.uuid4().hex,
                "status": "排队中",
                "processed": 0,
                "total": len(rows),
                "error": None,
                "stop_event": threading.Event(),
                "items": {str(row["crew_member_id"]): {**row, "status": "待查询", "error_info": None, "certificate_status": None, "attempts": 0} for row in rows},
            }
            _jobs[voyage_id] = job
        else:
            job = existing
    if existing is job:
        return _job_snapshot(job)

    thread = threading.Thread(target=_run_job, args=(voyage_id, rows, job, on_log), daemon=True)
    thread.start()
    return _job_snapshot(job)


def stop_job(voyage_id: int) -> dict[str, Any] | None:
    with _jobs_lock:
        job = _jobs.get(voyage_id)
        if not job:
            return None
        if job["status"] in {"排队中", "查询中"}:
            job["status"] = "停止中"
            job["stop_event"].set()
    return _job_snapshot(job)


def _run_job(voyage_id: int, rows: list[dict[str, Any]], job: dict[str, Any], on_log: Callable[[str], None] | None):
    with _jobs_lock:
        job["status"] = "查询中"

    def on_result(result: dict[str, Any]):
        member_id = int(result["crew_member_id"])
        with SessionLocal() as db:
            verification = db.scalars(
                select(SeafarerVerification).where(
                    SeafarerVerification.voyage_id == voyage_id,
                    SeafarerVerification.crew_member_id == member_id,
                )
            ).first()
            if not verification:
                verification = SeafarerVerification(voyage_id=voyage_id, crew_member_id=member_id)
                db.add(verification)
            for key in ("status", "website_certificate_no", "website_name", "certificate_status", "issuing_authority", "issue_date", "valid_date", "error_info", "attempts"):
                setattr(verification, key, result.get(key))
            verification.queried_at = datetime.utcnow()
            db.commit()
        with _jobs_lock:
            job["items"][str(member_id)].update(result)
            job["processed"] += 1

    try:
        SeafarerQueryRunner(on_log=on_log, stop_event=job["stop_event"]).run(rows, on_result)
        with _jobs_lock:
            job["status"] = "已停止" if job["stop_event"].is_set() else "已完成"
            if job["status"] == "已停止":
                for item in job["items"].values():
                    if item["status"] in {"待查询", "查询中"}:
                        item["status"] = "已停止"
                        item["error_info"] = "用户停止核验"
    except QueryCancelled:
        with _jobs_lock:
            job["status"] = "已停止"
            for item in job["items"].values():
                if item["status"] in {"待查询", "查询中"}:
                    item["status"] = "已停止"
                    item["error_info"] = "用户停止核验"
    except Exception as exc:
        with _jobs_lock:
            job["status"] = "失败"
            job["error"] = str(exc)[:500]
            for item in job["items"].values():
                if item["status"] in {"待查询", "查询中"}:
                    item["status"] = "失败"
                    item["error_info"] = str(exc)[:256]
