from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import stat
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Literal, Optional, Sequence, Tuple

import yaml
from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    InvalidSessionIdException,
    NoAlertPresentException,
    NoSuchElementException,
    NoSuchFrameException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from urllib3.exceptions import ReadTimeoutError
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

LOG = logging.getLogger("gisaid")

VirusType = Literal["A", "B"]


class GisaidDownloadError(RuntimeError):
    """Base error for download flow."""


class StepFailed(GisaidDownloadError):
    """Raised when a step fails after retries."""


@dataclass(frozen=True)
class DownloadConfig:
    # Credentials
    username: str
    password: str

    # Runtime
    download_root: Path
    headless: bool = False

    # Filters
    virus_type: VirusType = "A"
    h_types: Sequence[str] = field(default_factory=list)
    n_types: Sequence[str] = field(default_factory=list)
    b_lineages: Sequence[str] = field(default_factory=list)
    hosts: Sequence[str] = field(default_factory=list)
    submit_labs: Sequence[str] = field(default_factory=list)
    locations: Sequence[str] = field(default_factory=list)
    tpe_submissions: bool = False

    # Date ranges
    collection_date: Optional[Tuple[str, str]] = None  # ("YYYY-MM-DD", "YYYY-MM-DD")
    date_ranges: Sequence[Tuple[str, str]] = field(default_factory=list)
    max_strains_per_range: int = 20000

    # Download types
    download_dna: bool = False
    download_protein: bool = True
    download_metadata: bool = True
    require_manual_validation: bool = False

    # Sequence options
    segments: Sequence[str] = field(default_factory=lambda: ["HA"])
    replace_spaces_with_underscores: bool = True
    trim_fasta_values: bool = True
    dna_header: str = "Isolate ID | Virus name | Collection date | Type | Lineage | Segment"
    protein_header: str = "Isolate ID | Virus name | Collection date | Type | Lineage  | Gene name"

    # Timeouts
    page_timeout_sec: int = 40
    download_timeout_sec: int = 1800
    poll_interval_sec: int = 5
    step_retries: int = 3
    retry_delay_sec: int = 5

    def validate(self) -> None:
        if not self.username or not self.password:
            raise ValueError("username/password 不能为空。")
        if self.virus_type not in ("A", "B"):
            raise ValueError("virus_type 只能是 'A' 或 'B'。")
        if not self.download_root:
            raise ValueError("download_root 不能为空。")

        root = Path(self.download_root).expanduser().resolve()
        object.__setattr__(self, "download_root", root)

        if not self.date_ranges and not self.collection_date:
            raise ValueError("date_ranges 与 collection_date 至少提供一个。")

        collection_bounds: Optional[Tuple[datetime, datetime]] = None
        if self.collection_date:
            collection_start = _parse_date(self.collection_date[0])
            collection_end = _parse_date(self.collection_date[1])
            if collection_start > collection_end:
                raise ValueError("collection_date 起始日期不能晚于结束日期。")
            collection_bounds = (collection_start, collection_end)

        previous_end: Optional[datetime] = None
        for s, e in self.date_ranges:
            start = _parse_date(s)
            end = _parse_date(e)
            if start > end:
                raise ValueError(f"date_ranges 起始日期不能晚于结束日期: {s} - {e}")
            if previous_end is not None and start <= previous_end:
                raise ValueError("date_ranges 必须按日期升序排列且不能重叠。")
            if collection_bounds is not None and not (
                collection_bounds[0] <= start <= end <= collection_bounds[1]
            ):
                raise ValueError(f"date_ranges 超出 collection_date: {s} - {e}")
            previous_end = end

        if self.max_strains_per_range <= 0:
            raise ValueError("max_strains_per_range 必须为正整数。")
        if self.page_timeout_sec <= 0 or self.download_timeout_sec <= 0:
            raise ValueError("页面和下载超时必须为正整数。")
        if self.poll_interval_sec <= 0:
            raise ValueError("poll_interval_sec 必须为正整数。")
        if self.step_retries <= 0:
            raise ValueError("step_retries 必须为正整数。")
        if self.retry_delay_sec < 0:
            raise ValueError("retry_delay_sec 不能为负数。")


def _parse_date(date_str: str) -> datetime:
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"日期格式错误: {date_str!r}，应为 YYYY-MM-DD") from exc


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _safe_move(src: Path, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        shutil.move(str(src), str(dst))
        return dst

    stem, suffix = dst.stem, dst.suffix
    for i in range(1, 10_000):
        candidate = dst.with_name(f"{stem}.{i}{suffix}")
        if not candidate.exists():
            shutil.move(str(src), str(candidate))
            return candidate
    raise GisaidDownloadError(f"目标文件名冲突过多，无法移动到: {dst}")


def _write_date_ranges(
    config_path: Path, ranges: Sequence[Tuple[str, str]]
) -> None:
    """Atomically persist successfully calculated date ranges."""
    resolved_path = config_path.expanduser().resolve()
    try:
        with resolved_path.open("r", encoding="utf-8") as config_file:
            raw = yaml.safe_load(config_file) or {}
        if not isinstance(raw, dict):
            raise ValueError("配置顶层必须是映射。")

        dates = raw.setdefault("dates", {})
        if not isinstance(dates, dict):
            raise ValueError("dates 必须是映射。")
        dates["date_ranges"] = [[start, end] for start, end in ranges]

        original_mode = stat.S_IMODE(resolved_path.stat().st_mode)
        temporary_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=resolved_path.parent,
                prefix=f".{resolved_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                yaml.safe_dump(
                    raw,
                    temporary_file,
                    allow_unicode=True,
                    sort_keys=False,
                )
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            temporary_path.chmod(original_mode)
            os.replace(temporary_path, resolved_path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise GisaidDownloadError(
            f"自动日期分段已完成，但写回配置失败: {resolved_path}: {exc}"
        ) from exc


class GisaidEpiFluDownloader:
    BASE_URL = "https://www.epicov.org/epi3/frontend"

    # Selectors
    _SEL_LOGIN_USER = (By.ID, "elogin")
    _SEL_LOGIN_PASS = (By.ID, "epassword")
    _SEL_LOGIN_BTN = (By.XPATH, "//input[@value='Login']")
    _SEL_TAB_EPIFLU = (By.LINK_TEXT, "EpiFlu™")
    _SEL_ACTIONBAR_ITEM = (By.CLASS_NAME, "sys-actionbar-action-ni")
    _SEL_FILTER_LABEL = "//div[contains(@class, 'sys-form-filabel') and normalize-space(.)='{label}']"
    _SEL_DATEPICKER = (By.CLASS_NAME, "hasDatepicker")
    _SEL_TOTAL_INFO = (By.XPATH, "//div[contains(@class, 'sys-form-fi-info') and contains(., 'Total:')]")
    _SEL_SEARCH_BTN = (By.XPATH, "//button[normalize-space(text())='Search']")
    _SEL_SELECT_ALL = (By.XPATH, "(//input)[2]")
    _SEL_PLEASE_WAIT = (By.XPATH, "//div[normalize-space(text())='Please wait...']")
    _SEL_DOWNLOAD_BTN = (By.XPATH, "//button[normalize-space(text())='Download']")
    _SEL_DOWNLOAD_ANALYSIS = (By.XPATH, "//button[normalize-space(text())='Add to analysis']")
    _SEL_GO_BACK = (By.XPATH, "//button[normalize-space(text())='Go back']")

    # Agreement / iframe
    _FRAME_AGREE = "islemb"
    _FRAME_DOWNLOAD = "downl"
    _SEL_AGREE_BTN = (By.XPATH, "//button[normalize-space(text())='Yes, I agree']")
    _SEL_DOWNLOAD_TITLE = (By.XPATH, "//div[normalize-space(text())='Download']")

    # Download dialog
    _SEL_RADIO_DNA = (By.XPATH, "//input[@value='dna']")
    _SEL_RADIO_PROTEIN = (By.XPATH, "//input[@value='proteins']")
    _SEL_SEGMENT_ALL = (By.XPATH, "//input[@value='all']")
    _SEL_SEGMENT_VALUE = "//input[@value='{value}']"
    _SEL_HDR_INPUTS = (By.CLASS_NAME, "sys-fi-mark")
    _SEL_OPT_REPLACE_UNDERSCORE = (
        By.XPATH,
        "//span[contains(text(),'Replace spaces with underscores in FASTA header')]/preceding-sibling::input",
    )
    _SEL_OPT_TRIM_SPACES = (
        By.XPATH,
        "//span[contains(text(),'Remove spaces before and after values in FASTA header')]/preceding-sibling::input",
    )

    def __init__(
        self, config: DownloadConfig, *, config_path: Optional[Path] = None
    ) -> None:
        config.validate()
        self.cfg = config
        self.config_path = (
            config_path.expanduser().resolve() if config_path is not None else None
        )

        self.meta_dir = self.cfg.download_root / "meta"
        self.dna_dir = self.cfg.download_root / "DNA"
        self.protein_dir = self.cfg.download_root / "protein"

        if self.cfg.download_metadata:
            _ensure_dir(self.meta_dir)
        if self.cfg.download_dna:
            _ensure_dir(self.dna_dir)
        if self.cfg.download_protein:
            _ensure_dir(self.protein_dir)

        self.driver = self._create_driver()
        self._count_viruses_cached = lru_cache(maxsize=2048)(self._count_viruses_uncached)

    def close(self) -> None:
        try:
            self.driver.quit()
        except Exception:
            LOG.exception("driver.quit() 失败，忽略。")

    def run(self) -> None:
        try:
            self._run_step(self._login, "login")
            self._run_step(self._open_epiflu_search, "open_epiflu_search")
            self._run_step(self._apply_filters, "apply_filters")

            ranges = self._resolve_date_ranges()
            total_ranges = len(ranges)
            LOG.info("将下载 %d 个时间区间。", total_ranges)

            for idx, (start, end) in enumerate(ranges, 1):
                tag = f"{start}-{end}"
                pending_meta, pending_dna, pending_protein = self._pending_downloads(
                    start, end
                )
                if not any((pending_meta, pending_dna, pending_protein)):
                    LOG.info("[跳过] 区间 %s 的所选文件均已下载。", tag)
                    continue

                # 日志友好的进度条渲染
                percent = int((idx / total_ranges) * 100)
                bar_length = 25
                filled_length = int(bar_length * idx // total_ranges)
                bar = '█' * filled_length + '░' * (bar_length - filled_length)

                LOG.info("=" * 70)
                LOG.info("[当前进度]: |%s| %d%% (%d/%d)", bar, percent, idx, total_ranges)
                LOG.info("[当前任务]: 开始下载区间 %s", tag)
                LOG.info("=" * 70)

                LOG.info("%s start", tag)

                self._run_step(lambda: self._set_collection_dates(start, end), f"set_dates[{tag}]")
                self._run_step(self._search, f"search[{tag}]")
                self._run_step(self._select_all_results, f"select_all[{tag}]")

                # 下载 Metadata
                if pending_meta:
                    self._run_step(
                        self._open_download_dialog,
                        f"open_download_dialog_meta[{tag}]",
                    )
                    self._run_step(
                        lambda: self._download_metadata(start, end),
                        f"download_metadata[{tag}]",
                    )
                    self._run_step(
                        self._go_back_to_results,
                        f"go_back_to_results_after_meta[{tag}]",
                    )

                # 下载 Fasta (DNA / Protein)
                if pending_dna or pending_protein:
                    self._run_step(
                        self._open_download_dialog,
                        f"open_download_dialog_seq[{tag}]",
                    )
                    if pending_dna:
                        self._run_step(
                            lambda: self._download_fasta(start, end, kind="dna"),
                            f"download_dna[{tag}]",
                        )
                    if pending_protein:
                        self._run_step(
                            lambda: self._download_fasta(
                                start, end, kind="protein"
                            ),
                            f"download_protein[{tag}]",
                        )

                    LOG.info("dna/protein %s done", tag)
                    self._run_step(
                        self._go_back_to_results,
                        f"go_back_to_results_after_seq[{tag}]",
                    )

                # 从结果列表返回到搜索配置页，为下一个时间区间做准备
                self._run_step(self._go_back_to_search_page, f"go_back_to_search_page[{tag}]")

            LOG.info("全部完成。输出目录: %s", self.cfg.download_root)

        finally:
            self.close()

    # ---------------------- Core steps ----------------------

    def _login(self) -> None:
        self.driver.get(self.BASE_URL)
        self._wait_present(*self._SEL_LOGIN_USER, timeout=15).send_keys(self.cfg.username)
        self.driver.find_element(*self._SEL_LOGIN_PASS).send_keys(self.cfg.password)

        btn = self._wait_clickable(*self._SEL_LOGIN_BTN, timeout=15)
        self._wait_overlay_gone(timeout=self.cfg.page_timeout_sec)
        self.driver.execute_script("arguments[0].click();", btn)
        LOG.info("完成登录。")

    def _open_epiflu_search(self) -> None:
        tab = self._wait_present(*self._SEL_TAB_EPIFLU, timeout=20)
        self._wait_overlay_gone(timeout=self.cfg.page_timeout_sec)
        self.driver.execute_script(tab.get_attribute("onclick"))

        WebDriverWait(self.driver, 20).until(
            lambda d: any(e.text == "EpiFlu™" for e in d.find_elements(*self._SEL_ACTIONBAR_ITEM))
        )
        self._wait_overlay_gone(timeout=self.cfg.page_timeout_sec)
        items = self.driver.find_elements(*self._SEL_ACTIONBAR_ITEM)
        if len(items) < 2:
            raise GisaidDownloadError("未找到 EpiFlu Search 入口（actionbar 项不足）。")
        items[1].click()
        LOG.info("完成跳转到 EpiFlu Search。")

    def _apply_filters(self) -> None:
        if self.cfg.submit_labs:
            lab_select = self._filter_select_by_label("Submitting Laboratory")
            self._select_by_visible_text(lab_select, self.cfg.submit_labs)

        if self.cfg.hosts:
            host_select = self._filter_select_by_label("Host")
            self._select_by_visible_text(host_select, self.cfg.hosts)

        if self.cfg.locations:
            location_select = self._filter_select_by_label("Location")
            self._select_by_visible_text(location_select, self.cfg.locations)

        type_select = self._filter_select_by_label("Type")
        type_select.deselect_all()
        type_select.select_by_value(self.cfg.virus_type)
        self._wait_overlay_gone(timeout=self.cfg.page_timeout_sec)

        if self.cfg.virus_type == "A":
            if self.cfg.h_types:
                h_select = self._filter_select_by_label("H")
                h_select.deselect_all()
                for v in self.cfg.h_types:
                    h_select.select_by_value(v)
                    self._wait_overlay_gone(timeout=self.cfg.page_timeout_sec)

            if self.cfg.n_types:
                n_select = self._filter_select_by_label("N")
                n_select.deselect_all()
                for v in self.cfg.n_types:
                    n_select.select_by_value(v)
                    self._wait_overlay_gone(timeout=self.cfg.page_timeout_sec)

        if self.cfg.virus_type == "B" and self.cfg.b_lineages:
            b_select = self._filter_select_by_label("Lineage")
            b_select.deselect_all()
            for v in self.cfg.b_lineages:
                b_select.select_by_value(v)
                self._wait_overlay_gone(timeout=self.cfg.page_timeout_sec)

        if self.cfg.tpe_submissions:
            checkbox = self._filter_checkbox_by_label("TPE submissions")
            if not checkbox.is_selected():
                checkbox.click()
                self._wait_overlay_gone(timeout=self.cfg.page_timeout_sec)

        LOG.info("完成筛选条件设置。")

    # def _set_collection_dates(self, start: str, end: str) -> None:
    #     start_dt = _parse_date(start)
    #     end_dt = _parse_date(end)
    #     if start_dt > end_dt:
    #         raise ValueError(f"开始日期不能晚于结束日期: {start} > {end}")
    #
    #     elems = WebDriverWait(self.driver, 20).until(lambda d: d.find_elements(*self._SEL_DATEPICKER))
    #
    #     elems[0].clear()
    #     elems[1].clear()
    #     elems[0].send_keys(start)
    #     elems[1].send_keys(end)
    #     self._wait_overlay_gone(timeout=300)

    def _set_collection_dates(self, start: str, end: str) -> None:
        start_dt = _parse_date(start)
        end_dt = _parse_date(end)
        if start_dt > end_dt:
            raise ValueError(f"开始日期不能晚于结束日期: {start} > {end}")

        elems = WebDriverWait(self.driver, 20).until(lambda d: d.find_elements(*self._SEL_DATEPICKER))

        self.driver.execute_script(
            """
            arguments[0].value = arguments[2];
            arguments[1].value = arguments[3];

            arguments[0].dispatchEvent(new Event('change'));
            arguments[1].dispatchEvent(new Event('change'));
            arguments[0].dispatchEvent(new Event('blur'));
            arguments[1].dispatchEvent(new Event('blur'));
            """,
            elems[0], elems[1], start, end
        )
        self._wait_overlay_gone(timeout=300)

    def _search(self) -> None:
        btn = self._wait_clickable(*self._SEL_SEARCH_BTN, timeout=20)
        self.driver.execute_script("arguments[0].click();", btn)
        LOG.info("完成搜索。")

    def _select_all_results(self) -> None:
        self._wait_please_wait_done(timeout=60)
        checkbox = self._wait_clickable(*self._SEL_SELECT_ALL, timeout=20)
        self.driver.execute_script("arguments[0].click();", checkbox)
        self._wait_overlay_gone(timeout=self.cfg.page_timeout_sec)
        LOG.info("完成全选。")

    def _open_download_dialog(self) -> None:
        btn = self._wait_clickable(*self._SEL_DOWNLOAD_BTN, timeout=20)
        btn.click()

        if self.cfg.require_manual_validation:
            LOG.warning("require_manual_validation=True：请在浏览器中完成验证码/人工验证。")
            time.sleep(80)
        else:
            self._wait_overlay_gone(timeout=self.cfg.page_timeout_sec)

    def _download_metadata(self, start: str, end: str) -> None:
        self._accept_agreement_if_present()
        with self._frame(self._FRAME_DOWNLOAD, timeout=self.cfg.page_timeout_sec):
            self._wait_overlay_gone(timeout=self.cfg.page_timeout_sec)
            self._wait_present(*self._SEL_DOWNLOAD_TITLE, timeout=20)

            # 使用 JS 强制点击下载
            download_btn = self._wait_clickable(*self._SEL_DOWNLOAD_BTN, timeout=30)
            self.driver.execute_script("arguments[0].click();", download_btn)
            LOG.info("Metadata 下载指令已发送...")
            time.sleep(2)
            self._dismiss_alert_if_present()
        downloaded = self._wait_for_download(
            folder=self.cfg.download_root,
            name_contains="gisaid_epiflu",
            timeout=self.cfg.download_timeout_sec,
            poll=self.cfg.poll_interval_sec,
        )
        out = _safe_move(downloaded, self.meta_dir / f"{start}-{end}.xls")
        LOG.info("完成 metatable")
        LOG.info("【成功】Metadata 文件已成功改名并移动至: %s", out)

    def _download_fasta(self, start: str, end: str, kind: Literal["dna", "protein"]) -> None:
        self._accept_agreement_if_present()
        with self._frame(self._FRAME_DOWNLOAD, timeout=self.cfg.page_timeout_sec):
            self._wait_overlay_gone(timeout=self.cfg.page_timeout_sec)
            self._wait_present(*self._SEL_DOWNLOAD_TITLE, timeout=20)

            if kind == "dna":
                self._wait_clickable(*self._SEL_RADIO_DNA, timeout=20).click()
                header = self.cfg.dna_header
                out_dir = self.dna_dir
                out_ext = ".fasta"
            else:
                self._wait_clickable(*self._SEL_RADIO_PROTEIN, timeout=20).click()
                header = self.cfg.protein_header
                out_dir = self.protein_dir
                out_ext = ".fasta"

            self._wait_overlay_gone(timeout=self.cfg.page_timeout_sec)
            self._select_segments(self.cfg.segments)
            self._enable_fasta_header_options()
            self._set_fasta_header(header)

            self._wait_overlay_gone(timeout=self.cfg.page_timeout_sec)
            self._wait_present(*self._SEL_DOWNLOAD_TITLE, timeout=20)

            download_btn = self._wait_clickable(*self._SEL_DOWNLOAD_BTN, timeout=30)
            self.driver.execute_script("arguments[0].click();", download_btn)
            LOG.info("fasta 下载指令已发送...")
            time.sleep(2)
            self._dismiss_alert_if_present()

        downloaded = self._wait_for_download(
            folder=self.cfg.download_root,
            name_contains="gisaid_epiflu",
            timeout=self.cfg.download_timeout_sec,
            poll=self.cfg.poll_interval_sec,
        )
        out = _safe_move(downloaded, out_dir / f"{start}-{end}{out_ext}")
        LOG.info("完成 %s FASTA: %s", kind, out)

    # ---------------------- Navigation & Return Fixes ----------------------
    def _go_back_to_results(self) -> None:
        """从下载弹窗设置页返回到搜索出来的结果列表页"""
        LOG.info("尝试从下载弹窗返回结果列表...")
        try:
            with self._frame(self._FRAME_DOWNLOAD, timeout=5):
                self._go_back()
        except (TimeoutException, NoSuchFrameException):
            # LOG.info("未检测到下载弹窗，可能已自动返回结果列表。")
            pass
        # self.driver.switch_to.default_content()
        self._wait_overlay_gone(timeout=self.cfg.page_timeout_sec)
        time.sleep(5)

    def _go_back_to_search_page(self) -> None:
        """从结果列表页返回到最外层的搜索条件配置页"""
        LOG.info("尝试从结果列表返回搜索配置页...")
        self._go_back()
    def _go_back(self) -> None:
        btn = WebDriverWait(self.driver, 15).until(EC.element_to_be_clickable(self._SEL_GO_BACK))
        # 使用 JavaScript 点击规避潜在的隐形遮罩拦截
        self.driver.execute_script("arguments[0].click();", btn)

    # ---------------------- Date splitting ----------------------

    def _resolve_date_ranges(self) -> list[Tuple[str, str]]:
        if self.cfg.date_ranges:
            return list(self.cfg.date_ranges)

        ranges = list(self._auto_split_ranges_with_retries())
        if self.config_path is None:
            LOG.warning("未提供配置路径，自动日期分段不会写回 YAML。")
        else:
            _write_date_ranges(self.config_path, ranges)
            LOG.info("自动日期分段已写回配置: %s", self.config_path)
        return ranges

    def _auto_split_ranges_with_retries(self) -> Sequence[Tuple[str, str]]:
        retryable_errors = (
            GisaidDownloadError,
            TimeoutException,
            StaleElementReferenceException,
            NoSuchElementException,
            WebDriverException,
            ReadTimeoutError,
        )
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.cfg.step_retries + 1):
            try:
                return self._auto_split_ranges()
            except retryable_errors as exc:
                last_exc = exc
                self._count_viruses_cached.cache_clear()
                LOG.warning(
                    "自动日期分段失败 (attempt %d/%d): %s",
                    attempt,
                    self.cfg.step_retries,
                    exc,
                )
                if attempt < self.cfg.step_retries:
                    time.sleep(self.cfg.retry_delay_sec)
        raise StepFailed(
            f"自动日期分段连续失败 {self.cfg.step_retries} 次。"
        ) from last_exc

    def _auto_split_ranges(self) -> Sequence[Tuple[str, str]]:
        assert self.cfg.collection_date is not None
        start, end = self.cfg.collection_date
        ranges = self._split_ranges_by_max_strains(start, end, self.cfg.max_strains_per_range)
        yaml_lines = [
            "",
            "#" + "=" * 50,
            "# 自动日期分段结果（成功后写回当前配置的 date_ranges）:",
            "#" + "=" * 50
        ]
        for s_str, e_str in ranges:
            yaml_lines.append(f'    - ["{s_str}", "{e_str}"]')
        yaml_lines.append("#" + "=" * 50 + "\n")
        formatted_yaml = "\n".join(yaml_lines)
        LOG.info(formatted_yaml)

        LOG.info("自动分段完成：%d 段。", len(ranges))
        return ranges

    def _split_ranges_by_max_strains(
        self, start: str, end: str, max_strains: int
    ) -> Sequence[Tuple[str, str]]:
        start_dt = _parse_date(start)
        end_dt = _parse_date(end)

        def rec(s_dt: datetime, e_dt: datetime) -> Sequence[Tuple[datetime, datetime, int]]:
            s = s_dt.strftime("%Y-%m-%d")
            e = e_dt.strftime("%Y-%m-%d")
            total = self._count_viruses_cached(s, e)
            if total <= max_strains:
                return [(s_dt, e_dt, total)]
            if s_dt.date() == e_dt.date():
                raise GisaidDownloadError(
                    f"单日 {s} 包含 {total} 条记录，超过每段上限 {max_strains}，"
                    "无法继续拆分。"
                )

            mid = s_dt + timedelta(days=(e_dt - s_dt).days // 2)
            left_s, left_e = s_dt, mid
            right_s, right_e = mid + timedelta(days=1), e_dt

            left_total = self._count_viruses_cached(
                left_s.strftime("%Y-%m-%d"), left_e.strftime("%Y-%m-%d")
            )
            right_total = total - left_total

            left_parts = (
                rec(left_s, left_e)
                if left_total > max_strains
                else [(left_s, left_e, left_total)]
            )
            right_parts = (
                rec(right_s, right_e)
                if right_total > max_strains
                else [(right_s, right_e, right_total)]
            )
            return [*left_parts, *right_parts]

        parts = rec(start_dt, end_dt)

        merged: list[Tuple[datetime, datetime, int]] = []
        for s_dt, e_dt, n in parts:
            if not merged:
                merged.append((s_dt, e_dt, n))
                continue
            ps, pe, pn = merged[-1]
            if pn + n <= max_strains and (pe + timedelta(days=1) >= s_dt):
                merged[-1] = (ps, e_dt, pn + n)
            else:
                merged.append((s_dt, e_dt, n))

        out = [(s.strftime("%Y-%m-%d"), e.strftime("%Y-%m-%d")) for s, e, _ in merged]
        return out

    def _pending_downloads(self, start: str, end: str) -> Tuple[bool, bool, bool]:
        tag = f"{start}-{end}"

        def pending(enabled: bool, path: Path, label: str) -> bool:
            if not enabled:
                return False
            try:
                if path.is_file() and path.stat().st_size > 0:
                    LOG.info("[跳过] 已存在 %s 文件: %s", label, path)
                    return False
                if path.exists():
                    LOG.warning("发现空的 %s 文件，将重新下载: %s", label, path)
                    path.unlink()
            except OSError as exc:
                raise GisaidDownloadError(
                    f"检查已有下载文件失败: {path}: {exc}"
                ) from exc
            return True

        return (
            pending(
                self.cfg.download_metadata,
                self.meta_dir / f"{tag}.xls",
                "Metadata",
            ),
            pending(
                self.cfg.download_dna,
                self.dna_dir / f"{tag}.fasta",
                "DNA",
            ),
            pending(
                self.cfg.download_protein,
                self.protein_dir / f"{tag}.fasta",
                "Protein",
            ),
        )

    def _count_viruses_uncached(self, start: str, end: str) -> int:
        self._set_collection_dates(start, end)
        info = self._wait_present(*self._SEL_TOTAL_INFO, timeout=30).text
        m = re.search(r"Total:\s*(.+)\s*viruses", info)
        if not m:
            raise GisaidDownloadError(f"无法解析病毒数量文本: {info!r}")
        try:
            return int(m.group(1).replace(",", ""))
        except ValueError as exc:
            raise GisaidDownloadError(f"无法解析病毒数量文本: {info!r}") from exc

    # ---------------------- Driver Creation (Edge Only) ----------------------

    def _create_driver(self) -> webdriver.Remote:
        """专属创建 Edge 浏览器驱动，已配置各项静默下载与 Headless 策略"""
        download_dir = str(self.cfg.download_root)
        options = webdriver.EdgeOptions()

        options.add_experimental_option(
            "prefs",
            {
                "download.default_directory": os.path.abspath(download_dir),
                "download.prompt_for_download": False,
                "download.directory_upgrade": True,
                "safebrowsing.enabled": True,
            },
        )
        # 激活 Edge 的并行下载加速
        options.add_argument("--enable-features=ParallelDownloading")
        options.add_argument("--no-sandbox")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")

        if self.cfg.headless:
            options.add_argument("--headless=new")

        return webdriver.Edge(service=EdgeService(), options=options)

    # ---------------------- Utilities ----------------------

    def _run_step(self, fn, name: str) -> None:
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.cfg.step_retries + 1):
            try:
                fn()
                return
            except (
                TimeoutException,
                StaleElementReferenceException,
                NoSuchFrameException,
                ElementClickInterceptedException,
                NoSuchElementException,
                InvalidSessionIdException,
                WebDriverException,
                ReadTimeoutError,
            ) as exc:
                last_exc = exc
                LOG.warning(
                    "步骤失败: %s (attempt %d/%d): %s",
                    name,
                    attempt,
                    self.cfg.step_retries,
                    exc,
                )
                if attempt < self.cfg.step_retries:
                    time.sleep(self.cfg.retry_delay_sec)
        raise StepFailed(
            f"步骤 {name} 连续失败 {self.cfg.step_retries} 次。"
        ) from last_exc

    def _filter_select_by_label(self, label: str) -> Select:
        """按标签文本定位对应的下拉框（select）。

        GISAID 搜索页有两种标签布局：
        - 单字段行（如 "Submitting Laboratory"）：label 与 select 同处一个 <tr>；
        - 列式行（如 "Host"、"Type"、"H"、"N"、"Lineage"）：label 位于 label 行，
          select 位于相邻的 control 行，二者按列索引对应。

        先定位 text 为 label 的 div，再向上找到 td/tr；若该 tr 内含 select 直接返回，
        否则按列索引到 control 行取同列 td 内的 select。
        """
        label_div = self._wait_present(
            By.XPATH, self._SEL_FILTER_LABEL.format(label=label), timeout=20
        )
        label_td = label_div.find_element(By.XPATH, "./parent::td")
        label_tr = label_td.find_element(By.XPATH, "./parent::tr")

        if label_tr.find_elements(By.TAG_NAME, "select"):
            return self._enabled_select_in(label_tr)

        col_index = self._column_index(label_td)
        control_tr = label_tr.find_element(By.XPATH, "./following-sibling::tr[1]")
        control_tds = control_tr.find_elements(By.TAG_NAME, "td")
        if col_index >= len(control_tds):
            raise GisaidDownloadError(
                f"筛选控件列索引越界: label={label!r}, index={col_index}, 实际={len(control_tds)}"
            )
        return self._enabled_select_in(control_tds[col_index])

    def _filter_checkbox_by_label(self, label: str) -> WebElement:
        """按标签文本定位对应的复选框（如 "TPE submissions"）。"""
        label_div = self._wait_present(
            By.XPATH, self._SEL_FILTER_LABEL.format(label=label), timeout=20
        )
        label_td = label_div.find_element(By.XPATH, "./parent::td")
        label_tr = label_td.find_element(By.XPATH, "./parent::tr")

        if label_tr.find_elements(By.XPATH, ".//input[@type='checkbox']"):
            return label_tr.find_element(By.XPATH, ".//input[@type='checkbox']")

        col_index = self._column_index(label_td)
        control_tr = label_tr.find_element(By.XPATH, "./following-sibling::tr[1]")
        control_td = control_tr.find_elements(By.TAG_NAME, "td")[col_index]
        return control_td.find_element(By.XPATH, ".//input[@type='checkbox']")

    def _enabled_select_in(self, container: WebElement) -> Select:
        def _find_enabled_select(_):
            sel = container.find_element(By.TAG_NAME, "select")
            if sel.get_attribute("disabled") is None:
                return sel
            return None

        select_el = WebDriverWait(self.driver, 20).until(_find_enabled_select)
        return Select(select_el)

    def _column_index(self, cell: WebElement) -> int:
        return int(
            self.driver.execute_script(
                "var n = arguments[0], i = 0;"
                "while (n.previousElementSibling) { i += 1; n = n.previousElementSibling; }"
                "return i;",
                cell,
            )
        )

    def _select_by_visible_text(self, select: Select, values: Iterable[str]) -> None:
        for v in values:
            select.select_by_visible_text(v)
            self._wait_overlay_gone(timeout=self.cfg.page_timeout_sec)

    def _accept_agreement_if_present(self) -> None:
        try:
            WebDriverWait(self.driver, 5).until(EC.frame_to_be_available_and_switch_to_it((By.NAME, self._FRAME_AGREE)))
            self._wait_clickable(*self._SEL_AGREE_BTN, timeout=10).click()
        except (TimeoutException, NoSuchFrameException):
            return
        finally:
            self.driver.switch_to.default_content()

    def _dismiss_alert_if_present(self) -> None:
        """关闭可能出现的原生 alert/confirm 弹窗（如下载确认框），避免其阻塞后续 WebDriver 命令。"""
        try:
            self.driver.switch_to.alert.accept()
            LOG.info("已关闭页面弹出确认框。")
        except NoAlertPresentException:
            return

    def _select_segments(self, segments: Sequence[str]) -> None:
        segments_norm = [s.strip() for s in segments if s and s.strip()]
        if not segments_norm:
            return

        if "all" in (s.lower() for s in segments_norm):
            btn = self._wait_clickable(*self._SEL_SEGMENT_ALL, timeout=20)
            if not btn.is_selected():
                btn.click()
                self._wait_overlay_gone(timeout=self.cfg.page_timeout_sec)
            return

        for seg in segments_norm:
            xpath = self._SEL_SEGMENT_VALUE.format(value=seg)
            btn = self._wait_clickable(By.XPATH, xpath, timeout=20)
            if not btn.is_selected():
                btn.click()
                self._wait_overlay_gone(timeout=self.cfg.page_timeout_sec)

    def _enable_fasta_header_options(self) -> None:
        if self.cfg.replace_spaces_with_underscores:
            checkbox = self._wait_present(*self._SEL_OPT_REPLACE_UNDERSCORE, timeout=20)
            if not checkbox.is_selected():
                checkbox.click()
                self._wait_overlay_gone(timeout=self.cfg.page_timeout_sec)
        if self.cfg.trim_fasta_values:
            checkbox = self._wait_present(*self._SEL_OPT_TRIM_SPACES, timeout=20)
            if not checkbox.is_selected():
                checkbox.click()
                self._wait_overlay_gone(timeout=self.cfg.page_timeout_sec)

    def _set_fasta_header(self, header: str) -> None:
        elems = WebDriverWait(self.driver, 20).until(lambda d: d.find_elements(*self._SEL_HDR_INPUTS))
        if len(elems) < 3:
            raise GisaidDownloadError("FASTA header 输入框定位失败（sys-fi-mark 数量不足）。")
        header_input = elems[2]
        header_input.clear()
        header_input.send_keys(header)
        self._wait_overlay_gone(timeout=self.cfg.page_timeout_sec)

    def _latest_file(self, folder: Path) -> Optional[Path]:
        try:
            files = [p for p in folder.iterdir() if p.is_file()]
        except FileNotFoundError:
            return None
        if not files:
            return None
        return max(files, key=lambda p: p.stat().st_ctime)

    def _wait_for_download(self, folder: Path, name_contains: str, timeout: int, poll: int) -> Path:
        start_time = time.time()
        LOG.info(f"正在监控下载目录，等待包含关键字 '{name_contains}' 的新文件落地...")

        while True:
            latest = self._latest_file(folder)
            if latest and name_contains in latest.name:
                if latest.stat().st_ctime >= start_time:
                    name = latest.name
                    if not name.endswith((".crdownload", ".part", ".tmp")):
                        try:
                            latest.open("r").close()
                            LOG.info(f"检测到新文件已完全下载并释放锁: {latest.name}")
                            return latest
                        except (PermissionError, IOError):
                            pass
            if time.time() - start_time > timeout:
                raise TimeoutError("下载超时或未检测到本次生命周期内生成的新文件。")
            time.sleep(poll)

    def _wait_please_wait_done(self, timeout: int) -> None:
        try:
            WebDriverWait(self.driver, 10).until(EC.presence_of_element_located(self._SEL_PLEASE_WAIT))
            WebDriverWait(self.driver, timeout).until(EC.invisibility_of_element_located(self._SEL_PLEASE_WAIT))
        except TimeoutException:
            return

    def _wait_overlay_gone(self, timeout: int) -> None:
        def _gone(locator: Tuple[str, str]) -> bool:
            try:
                el = self.driver.find_element(*locator)
                return not el.is_displayed()
            except NoSuchElementException:
                return True

        time.sleep(0.3)
        for loc in ((By.ID, "sys_curtain"), (By.ID, "sys_timer")):
            WebDriverWait(self.driver, timeout).until(lambda _: _gone(loc))
        time.sleep(0.3)

    def _wait_present(self, by: str, value: str, timeout: int) -> WebElement:
        return WebDriverWait(self.driver, timeout).until(EC.presence_of_element_located((by, value)))

    def _wait_clickable(self, by: str, value: str, timeout: int) -> WebElement:
        return WebDriverWait(self.driver, timeout).until(EC.element_to_be_clickable((by, value)))

    def _wait_invisible(self, by: str, value: str, timeout: int) -> None:
        WebDriverWait(self.driver, timeout).until(EC.invisibility_of_element_located((by, value)))

    class _FrameCtx:
        def __init__(self, outer: "GisaidEpiFluDownloader", name: str, timeout: int) -> None:
            self.outer = outer
            self.name = name
            self.timeout = timeout

        def __enter__(self):
            WebDriverWait(self.outer.driver, self.timeout).until(
                EC.frame_to_be_available_and_switch_to_it((By.NAME, self.name))
            )
            return self.outer.driver

        def __exit__(self, exc_type, exc, tb):
            self.outer.driver.switch_to.default_content()
            return False

    def _frame(self, name: str, timeout: int) -> "_FrameCtx":
        return self._FrameCtx(self, name, timeout)


def _date_pair(value: object, label: str) -> Optional[Tuple[str, str]]:
    if value in (None, []):
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{label} 必须包含两个 YYYY-MM-DD 日期。")
    return str(value[0]), str(value[1])


def load_download_config(config_file: Path) -> DownloadConfig:
    """Load and validate one downloader YAML configuration."""
    config_path = config_file.expanduser().resolve()
    if not config_path.is_file():
        raise FileNotFoundError(f"找不到配置文件: {config_path}")

    with config_path.open("r", encoding="utf-8") as config_stream:
        raw = yaml.safe_load(config_stream) or {}
    if not isinstance(raw, dict):
        raise ValueError("配置顶层必须是映射。")

    sections = {}
    for section_name in ("credentials", "runtime", "filters", "dates", "options"):
        section = raw.get(section_name)
        if section is None:
            section = {}
        if not isinstance(section, dict):
            raise ValueError(f"{section_name} 必须是映射。")
        sections[section_name] = section

    credentials = sections["credentials"]
    runtime = sections["runtime"]
    filters = sections["filters"]
    dates = sections["dates"]
    options = sections["options"]

    download_root_value = runtime.get("download_root")
    if not download_root_value:
        raise ValueError("配置缺少 runtime.download_root。")

    collection_date = _date_pair(dates.get("collection_date"), "collection_date")
    raw_ranges = dates.get("date_ranges") or []
    if not isinstance(raw_ranges, list):
        raise ValueError("date_ranges 必须是列表。")
    date_ranges = []
    for index, raw_range in enumerate(raw_ranges):
        date_pair = _date_pair(raw_range, f"date_ranges[{index}]")
        if date_pair is None:
            raise ValueError(f"date_ranges[{index}] 不能为空。")
        date_ranges.append(date_pair)

    cfg = DownloadConfig(
        username=str(credentials.get("username") or ""),
        password=str(credentials.get("password") or ""),
        download_root=Path(str(download_root_value)),
        headless=bool(runtime.get("headless", False)),
        virus_type=str(filters.get("virus_type", "A")),
        h_types=filters.get("h_types", ["1"]),
        n_types=filters.get("n_types", ["1"]),
        b_lineages=filters.get("b_lineages", []),
        hosts=filters.get("hosts", ["Human"]),
        submit_labs=filters.get("submit_labs", []),
        locations=filters.get("locations", []),
        tpe_submissions=bool(filters.get("tpe_submissions", False)),
        segments=filters.get("segments", ["HA"]),
        collection_date=collection_date,
        date_ranges=date_ranges,
        max_strains_per_range=int(dates.get("max_strains_per_range", 20000)),
        download_dna=bool(options.get("download_dna", False)),
        download_protein=bool(options.get("download_protein", True)),
        download_metadata=bool(options.get("download_metadata", True)),
        require_manual_validation=bool(
            options.get("require_manual_validation", False)
        ),
        replace_spaces_with_underscores=bool(
            options.get("replace_spaces_with_underscores", True)
        ),
        trim_fasta_values=bool(options.get("trim_fasta_values", True)),
        page_timeout_sec=int(runtime.get("page_timeout_sec", 40)),
        download_timeout_sec=int(runtime.get("download_timeout_sec", 1800)),
        poll_interval_sec=int(runtime.get("poll_interval_sec", 5)),
        step_retries=int(runtime.get("step_retries", 3)),
        retry_delay_sec=int(runtime.get("retry_delay_sec", 5)),
    )
    cfg.validate()
    return cfg


def main(argv: Optional[Sequence[str]] = None) -> int:
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # 创建控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root_logger.addHandler(console_handler)

    # argparse 解析位置参数
    parser = argparse.ArgumentParser(description="GISAID EpiFlu Download")
    parser.add_argument("config_file", type=Path, help="YAML 配置文件")
    args = parser.parse_args(argv)

    try:
        config_path = args.config_file.expanduser().resolve()
        cfg = load_download_config(config_path)
        cfg.download_root.mkdir(parents=True, exist_ok=True)

        log_file_path = cfg.download_root / "gisaid_run.log"
        file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root_logger.addHandler(file_handler)

        downloader = GisaidEpiFluDownloader(cfg, config_path=config_path)
        downloader.run()
    except KeyboardInterrupt:
        LOG.warning("用户中断下载；重新运行同一配置将跳过已完成文件。")
        return 130
    except Exception as exc:
        LOG.exception("下载失败: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
