"""剪映自动化控制，主要与自动导出有关"""

import time
import shutil
import sys

# 平台检查和依赖导入
if sys.platform != "win32":
    raise ImportError("JianyingController is only available on Windows platform")

try:
    import uiautomation as uia
except ImportError as e:
    raise ImportError(f"Missing required Windows dependencies: {e}. Please install with: pip install capcut-mate[windows]")

try:
    import pyautogui  # pyright: ignore[reportMissingModuleSource]
except ImportError as e:
    raise ImportError(f"Missing required Windows dependencies: {e}. Please install with: pip install pyautogui[windows]")

from enum import Enum
from typing import Optional, Literal, Callable

from . import exceptions
from .exceptions import AutomationError

# 添加logger导入
from src.utils.logger import logger

class ExportResolution(Enum):
    """导出分辨率"""
    RES_8K = "8K"
    RES_4K = "4K"
    RES_2K = "2K"
    RES_1080P = "1080P"
    RES_720P = "720P"
    RES_480P = "480P"

class ExportFramerate(Enum):
    """导出帧率"""
    FR_24 = "24fps"
    FR_25 = "25fps"
    FR_30 = "30fps"
    FR_50 = "50fps"
    FR_60 = "60fps"

class ControlFinder:
    """控件查找器，封装部分与控件查找相关的逻辑"""

    @staticmethod
    def desc_matcher(target_desc: str, depth: int = 2, exact: bool = False) -> Callable[[uia.Control, int], bool]:
        """根据full_description查找控件的匹配器"""
        target_desc = target_desc.lower()
        def matcher(control: uia.Control, _depth: int) -> bool:
            if _depth != depth:
                return False
            full_desc: str = control.GetPropertyValue(30159).lower()
            return (target_desc == full_desc) if exact else (target_desc in full_desc)
        return matcher

    @staticmethod
    def class_name_matcher(class_name: str, depth: int = 1, exact: bool = False) -> Callable[[uia.Control, int], bool]:
        """根据ClassName查找控件的匹配器"""
        class_name = class_name.lower()
        def matcher(control: uia.Control, _depth: int) -> bool:
            if _depth != depth:
                return False
            curr_class_name: str = control.ClassName.lower()
            return (class_name == curr_class_name) if exact else (class_name in curr_class_name)
        return matcher

class JianyingController:
    """剪映控制器"""

    app: uia.WindowControl
    """剪映窗口"""
    app_status: Literal["home", "edit", "pre_export"]
    """当app_status为pre_export时，app_sub_status表示导出过程中的子状态"""
    app_sub_status: Literal["none", "export_start", "exporting", "export_succeed"]

    SUBTITLE_TOPBAR_ENTRY_CANDIDATES = [
        "字幕",
        "文本",
        "Caption",
        "Captions",
    ]
    SUBTITLE_PANEL_ENTRY_CANDIDATES = [
        "VETreeMainCellItem:识别字幕",
        "VEFreeMainCellItem:识别字幕",
        "识别字幕",
        "自动字幕",
        "智能字幕",
        "智能识别字幕",
        "字幕识别",
        "Recognize subtitles",
        "Auto captions",
    ]
    AUDIO_TRACK_SELECTION_CANDIDATES = [
        "MTLSAudioP:",
        "MTLSAudio:",
        "MTLSAudio",
        ".wav",
        ".mp3",
        ".m4a",
        ".aac",
    ]

    def __init__(self):
        """初始化剪映控制器, 此时剪映应该处于目录页"""
        self.get_window()

    def find_and_click_draft(self, draft_name: str, max_retries: int = 5, retry_interval: float = 5.0) -> None:
        """查找并点击指定名称的草稿
        
        Args:
            draft_name (str): 要查找的草稿名称
            max_retries (int): 最大重试次数，默认5次
            retry_interval (float): 重试间隔时间(秒)，默认5秒
            
        Raises:
            DraftNotFound: 未找到指定名称的剪映草稿
        """
        last_exception = None
        for attempt in range(max_retries):
            try:
                # 点击对应草稿
                draft_name_text = self.app.TextControl(
                    searchDepth=2,
                    Compare=ControlFinder.desc_matcher(f"HomePageDraftTitle:{draft_name}", exact=True)
                )
                if not draft_name_text.Exists(0):
                    if attempt == 0:
                        titles = self.log_home_draft_titles()
                        if not titles:
                            raise exceptions.DraftNotFound(
                                "当前剪映版本未向 UI 自动化暴露主页草稿标题控件，"
                                "无法自动定位草稿；草稿已写入剪映目录，请在剪映内手动打开。"
                            )
                    raise exceptions.DraftNotFound(f"未找到名为{draft_name}的剪映草稿")
                draft_btn = draft_name_text.GetParentControl()
                assert draft_btn is not None
                rect = draft_btn.BoundingRectangle
                x = (rect.left + rect.right) // 2
                y = (rect.top + rect.bottom) // 2
                pyautogui.doubleClick(x=x, y=y, interval=0.12, button="left")
                logger.info("double clicked draft card: %s at (%s, %s)", draft_name, x, y)
                if self.wait_until_edit_page(timeout=18):
                    return
                raise exceptions.DraftNotFound(f"草稿 {draft_name} 点击后未进入编辑页")
            except exceptions.DraftNotFound as e:
                last_exception = e
                if attempt < max_retries - 1:
                    logger.info(f"未找到名为{draft_name}的剪映草稿，第{attempt + 1}次重试...")
                    time.sleep(retry_interval)
        
        # 所有重试都失败，抛出异常
        raise last_exception

    def wait_until_edit_page(self, timeout: float = 18) -> bool:
        """等待剪映从首页进入编辑页。"""
        started = time.time()
        while time.time() - started <= timeout:
            time.sleep(1)
            try:
                self.get_window()
            except AutomationError:
                continue
            if self.app_status == "edit":
                logger.info("draft opened into edit page")
                return True
            upgrade = self.find_control_by_desc("升级", include_desktop=True, max_depth=6) or self.find_control_by_desc("版本过低", include_desktop=True, max_depth=6)
            if upgrade:
                raise AutomationError("剪映提示版本/升级弹窗，已停止自动点击，避免误升级。")
        return False

    def find_control_by_desc(self, desc: str, *, max_depth: int = 8, exact: bool = False, include_desktop: bool = False):
        """按 full_description 遍历查找控件。剪映 5.9 部分控件 TextControl 直搜不稳定，遍历更稳。"""
        target = desc.lower()

        def walk(control, depth: int = 0):
            if depth > max_depth:
                return None
            try:
                full_desc = str(control.GetPropertyValue(30159) or "")
                current = full_desc.lower()
                if (current == target) if exact else (target in current):
                    return control
                child = control.GetFirstChildControl()
                while child:
                    found = walk(child, depth + 1)
                    if found:
                        return found
                    child = child.GetNextSiblingControl()
            except Exception:
                return None
            return None

        found = walk(self.app)
        if found or not include_desktop:
            return found
        return walk(uia.GetRootControl())

    def click_desc(self, desc: str, *, exact: bool = False, wait: float = 1.0, include_desktop: bool = False) -> None:
        """点击指定 description 的控件，优先 UIA Click，失败时点击控件中心点。"""
        control = self.find_control_by_desc(desc, exact=exact, include_desktop=include_desktop)
        if not control:
            raise AutomationError(f"未找到控件：{desc}")
        try:
            control.Click(simulateMove=False)
        except Exception:
            rect = control.BoundingRectangle
            pyautogui.click(x=(rect.left + rect.right) // 2, y=(rect.top + rect.bottom) // 2, button="left")
        time.sleep(wait)

    def click_any_desc(self, candidates: list[str], *, wait: float = 1.0, include_desktop: bool = False, max_depth: int = 8) -> str:
        """点击任一匹配 description 的控件，返回命中的候选词。"""
        for desc in candidates:
            control = self.find_control_by_desc(desc, include_desktop=include_desktop, max_depth=max_depth)
            if control:
                try:
                    control.Click(simulateMove=False)
                except Exception:
                    rect = control.BoundingRectangle
                    pyautogui.click(x=(rect.left + rect.right) // 2, y=(rect.top + rect.bottom) // 2, button="left")
                time.sleep(wait)
                return desc
        raise AutomationError(f"未找到任一控件：{candidates}")

    def control_text_values(self, control) -> list[str]:
        """Collect stable UIA text fields exposed by different Jianying builds."""
        values: list[str] = []
        for getter in (
            lambda: control.GetPropertyValue(30159),
            lambda: control.GetPropertyValue(30005),
            lambda: control.Name,
            lambda: control.ClassName,
        ):
            try:
                value = str(getter() or "").strip()
            except Exception:
                value = ""
            if value and value not in values:
                values.append(value)
        return values

    def find_control_by_text(self, text: str, *, max_depth: int = 8, exact: bool = False, include_desktop: bool = False):
        """Find by full description, name, or class text. Jianying 5.9 changes these fields between items."""
        target = text.lower()

        def walk(control, depth: int = 0):
            if depth > max_depth:
                return None
            try:
                for value in self.control_text_values(control):
                    current = value.lower()
                    if (current == target) if exact else (target in current):
                        return control
                child = control.GetFirstChildControl()
                while child:
                    found = walk(child, depth + 1)
                    if found:
                        return found
                    child = child.GetNextSiblingControl()
            except Exception:
                return None
            return None

        found = walk(self.app)
        if found or not include_desktop:
            return found
        return walk(uia.GetRootControl())

    def click_control(self, control, *, wait: float = 1.0) -> None:
        try:
            control.Click(simulateMove=False)
        except Exception:
            rect = control.BoundingRectangle
            pyautogui.click(x=(rect.left + rect.right) // 2, y=(rect.top + rect.bottom) // 2, button="left")
        time.sleep(wait)

    def click_text_if_visible(
        self,
        candidates: list[str],
        *,
        wait: float = 1.0,
        include_desktop: bool = False,
        max_depth: int = 8,
    ) -> Optional[str]:
        for text in candidates:
            control = self.find_control_by_text(text, include_desktop=include_desktop, max_depth=max_depth)
            if control:
                self.click_control(control, wait=wait)
                return text
        return None

    def dump_visible_control_descriptions(self, reason: str, *, max_depth: int = 7, limit: int = 120) -> list[str]:
        """Log visible UIA text so unsupported Jianying UI changes can be adapted from real evidence."""
        rows: list[str] = []

        def walk(control, depth: int = 0) -> None:
            if depth > max_depth or len(rows) >= limit:
                return
            try:
                values = self.control_text_values(control)
                if values:
                    rows.append(" | ".join(values[:3]))
                child = control.GetFirstChildControl()
                while child:
                    walk(child, depth + 1)
                    child = child.GetNextSiblingControl()
            except Exception:
                return

        walk(self.app)
        logger.info("visible Jianying controls for %s: %s", reason, rows)
        return rows

    def right_click_control(self, control, *, wait: float = 0.8) -> None:
        """右键点击控件中心点。"""
        rect = control.BoundingRectangle
        x = (rect.left + rect.right) // 2
        y = (rect.top + rect.bottom) // 2
        pyautogui.click(x=x, y=y, button="right")
        time.sleep(wait)

    def try_context_menu_recognize_subtitles(self) -> bool:
        """优先通过音频片段右键菜单触发“识别字幕/歌词”。"""
        logger.info("try recognize subtitles from audio context menu")
        audio_desc_candidates = [
            "MTLSAudio",
            "AudioClip",
            "audio_clip",
            "音频",
            ".mp3",
            ".wav",
            ".m4a",
        ]
        menu_candidates = [
            "识别字幕/歌词",
            "字幕/歌词",
            "识别字幕",
            "智能识别字幕",
            "智能字幕",
        ]

        for desc in audio_desc_candidates:
            control = self.find_control_by_desc(desc, max_depth=12)
            if not control:
                continue
            self.right_click_control(control)
            try:
                clicked = self.click_any_desc(menu_candidates, wait=1.0, include_desktop=True, max_depth=4)
                logger.info("recognize subtitles context menu clicked by control %s: %s", desc, clicked)
                self.click_subtitle_recognition_start_button()
                return True
            except AutomationError:
                pyautogui.press("esc")
                time.sleep(0.3)

        # 兜底：剪映时间线里音频轨道通常在窗口下方，右键若干个常见位置。
        rect = self.app.BoundingRectangle
        width = max(1, rect.right - rect.left)
        height = max(1, rect.bottom - rect.top)
        fallback_points = [
            (rect.left + int(width * 0.38), rect.bottom - int(height * 0.15)),
            (rect.left + int(width * 0.48), rect.bottom - int(height * 0.15)),
            (rect.left + int(width * 0.38), rect.bottom - int(height * 0.20)),
            (rect.left + int(width * 0.28), rect.bottom - int(height * 0.15)),
        ]
        for x, y in fallback_points:
            logger.info("fallback right click audio timeline point: (%s, %s)", x, y)
            pyautogui.click(x=x, y=y, button="right")
            time.sleep(0.8)
            try:
                clicked = self.click_any_desc(menu_candidates, wait=1.0, include_desktop=True, max_depth=4)
                logger.info("recognize subtitles context menu clicked by fallback point: %s", clicked)
                self.click_subtitle_recognition_start_button()
                return True
            except AutomationError:
                pyautogui.press("esc")
                time.sleep(0.3)

        return False

    def trigger_subtitle_recognition_from_sidebar(self) -> None:
        """通过“字幕 -> 识别字幕”触发字幕识别。

        剪映 5.9 的字幕入口在顶部功能栏的“字幕”菜单下，不在“智能识别”树节点里。
        先走 UIA 文本/description，顶部栏不暴露文本时再用窗口比例点做兜底探测，避免依赖单个固定点位。
        """
        self.trigger_subtitle_recognition_from_toolbar()
        self.click_subtitle_panel_entry()
        self.click_subtitle_recognition_start_button()

    def select_audio_track_for_subtitle_recognition(self) -> None:
        """Select the narration audio clip before asking Jianying to recognize subtitles."""
        clicked = self.click_text_if_visible(
            self.AUDIO_TRACK_SELECTION_CANDIDATES,
            wait=0.8,
            max_depth=12,
        )
        if clicked:
            logger.info("audio track selected before subtitle recognition by UIA text: %s", clicked)
            return

        rect = self.app.BoundingRectangle
        width = max(1, rect.right - rect.left)
        height = max(1, rect.bottom - rect.top)
        # Jianying 5.9 does not consistently expose audio clips to UIA. These points target
        # the visible narration-audio lane, after the playhead and away from track controls.
        for x_ratio, y_ratio in ((0.28, 0.86), (0.38, 0.86), (0.50, 0.86), (0.28, 0.83), (0.38, 0.83)):
            x = rect.left + int(width * x_ratio)
            y = rect.top + int(height * y_ratio)
            logger.info("probe-select audio track before subtitle recognition: %.2f %.2f at (%s, %s)", x_ratio, y_ratio, x, y)
            pyautogui.click(x=x, y=y, button="left")
            time.sleep(0.45)
            return

        self.dump_visible_control_descriptions("audio-track-selection-missing", max_depth=9)
        raise AutomationError("未找到音频轨道，已停止智能字幕识别")

    def trigger_subtitle_recognition_from_toolbar(self) -> None:
        clicked = self.click_text_if_visible(self.SUBTITLE_TOPBAR_ENTRY_CANDIDATES, wait=0.8, max_depth=6)
        if clicked and self.has_subtitle_panel_entry():
            logger.info("subtitle toolbar entry clicked by UIA text: %s", clicked)
            return
        if clicked:
            logger.info("subtitle toolbar UIA candidate did not open subtitle panel: %s", clicked)

        rect = self.app.BoundingRectangle
        width = max(1, rect.right - rect.left)
        height = max(1, rect.bottom - rect.top)
        y_candidates = [
            rect.top + min(max(int(height * 0.05), 38), 72),
            rect.top + min(max(int(height * 0.065), 48), 86),
        ]
        for ratio in (0.16, 0.18, 0.19, 0.197, 0.205, 0.215, 0.225, 0.24, 0.26):
            for y in y_candidates:
                x = rect.left + int(width * ratio)
                logger.info("probe subtitle toolbar entry by relative point: %.3f at (%s, %s)", ratio, x, y)
                pyautogui.click(x=x, y=y, button="left")
                time.sleep(0.45)
                if self.has_subtitle_panel_entry():
                    return

        self.dump_visible_control_descriptions("subtitle-toolbar-entry-missing")
        raise AutomationError("未找到剪映字幕入口")

    def has_subtitle_panel_entry(self) -> bool:
        for candidate in self.SUBTITLE_PANEL_ENTRY_CANDIDATES:
            if self.find_control_by_text(candidate, max_depth=9):
                return True
        return False

    def click_subtitle_panel_entry(self) -> str:
        clicked = self.click_text_if_visible(self.SUBTITLE_PANEL_ENTRY_CANDIDATES, wait=1.2, max_depth=9)
        if clicked:
            logger.info("subtitle recognition panel entry clicked: %s", clicked)
            return clicked
        self.dump_visible_control_descriptions("subtitle-panel-entry-missing")
        raise AutomationError(f"未找到控件：{self.SUBTITLE_PANEL_ENTRY_CANDIDATES[0]}")

    def click_subtitle_recognition_start_button(self) -> None:
        """点击识别字幕面板里的“开始识别”按钮。"""
        rect = self.app.BoundingRectangle
        try:
            clicked = self.click_any_desc([
                "开始识别",
                "开始匹配",
                "SmartCaption",
                "Recognize",
                "Start",
                "Confirm",
                "OK",
            ], wait=1.0, include_desktop=True, max_depth=5)
            logger.info("recognize subtitle start button clicked: %s", clicked)
        except AutomationError:
            # 5.9 中“开始识别”按钮经常不暴露描述，按识别字幕面板中的蓝色主按钮兜底。
            button = self.find_blue_button_in_region(
                rect.left + 80,
                rect.top + 180,
                rect.left + 840,
                rect.top + 600,
            )
            if button:
                x, y = button
            else:
                x = rect.left + 500
                y = rect.top + 435
            logger.warning("recognize subtitle start button not exposed, fallback click at (%s, %s)", x, y)
            pyautogui.click(x=x, y=y, button="left")
            time.sleep(1)

    def find_blue_button_in_region(self, left: int, top: int, right: int, bottom: int) -> Optional[tuple[int, int]]:
        """在指定区域中找剪映蓝色主按钮中心点。"""
        try:
            image = pyautogui.screenshot()
            pixels = image.load()
            step = 2
            visited: set[tuple[int, int]] = set()
            best: tuple[int, int, int, int, int] | None = None

            def is_primary_blue(x: int, y: int) -> bool:
                red, green, blue = pixels[x, y][:3]
                return blue > 140 and green > 80 and red < 130 and blue - red > 45

            for y in range(max(0, top), min(image.height, bottom), step):
                for x in range(max(0, left), min(image.width, right), step):
                    if (x, y) in visited or not is_primary_blue(x, y):
                        continue
                    queue = [(x, y)]
                    visited.add((x, y))
                    xs: list[int] = []
                    ys: list[int] = []
                    while queue:
                        cx, cy = queue.pop()
                        xs.append(cx)
                        ys.append(cy)
                        for nx, ny in ((cx + step, cy), (cx - step, cy), (cx, cy + step), (cx, cy - step)):
                            if nx < left or nx >= right or ny < top or ny >= bottom or (nx, ny) in visited:
                                continue
                            if is_primary_blue(nx, ny):
                                visited.add((nx, ny))
                                queue.append((nx, ny))
                    if len(xs) >= 20:
                        box = (min(xs), min(ys), max(xs), max(ys), len(xs))
                        if best is None or box[4] > best[4]:
                            best = box
            if not best:
                return None
            return ((best[0] + best[2]) // 2, (best[1] + best[3]) // 2)
        except Exception as exc:
            logger.warning("failed to locate blue subtitle button: %s", exc)
            return None

    def recognize_subtitles(self, draft_name: str, timeout: float = 180) -> None:
        """在剪映 5.9 中打开草稿并触发“智能识别 -> 识别字幕”。

        这个动作依赖剪映 UI 自动化。识别结果由剪映自身写入草稿。
        """
        logger.info("start recognize subtitles for draft: %s", draft_name)
        self.get_window()
        self.switch_to_home()
        self.__ensure_window_focus()
        self.find_and_click_draft(draft_name)
        self.__ensure_window_focus()
        self.select_audio_track_for_subtitle_recognition()

        try:
            self.trigger_subtitle_recognition_from_sidebar()
        except AutomationError:
            logger.info("sidebar recognition failed, fallback to audio context menu recognition")
            if not self.try_context_menu_recognize_subtitles():
                raise

        self.wait_for_subtitle_recognition(timeout)
        logger.info("recognize subtitles finished for draft: %s", draft_name)

    def wait_for_subtitle_recognition(self, timeout: float = 180) -> None:
        """等待字幕识别结束。能看到时间线字幕片段即认为成功；否则等按钮/进度状态消失。"""
        start = time.time()
        seen_recognizing = False
        while time.time() - start <= timeout:
            self.get_window()
            if self.find_control_by_desc("MTLSTextP:") or self.find_control_by_desc("MTLSText:"):
                return
            if self.find_control_by_desc("识别完成") or self.find_control_by_desc("完成"):
                return
            if self.find_control_by_desc("识别中") or self.find_control_by_desc("正在识别"):
                seen_recognizing = True
                time.sleep(2)
                continue
            if seen_recognizing and time.time() - start > 8:
                return
            if time.time() - start > 20:
                logger.info("subtitle recognition status not exposed, assume triggered after 20 seconds")
                return
            time.sleep(2)
        raise AutomationError("智能字幕识别超时")

    def log_home_draft_titles(self) -> list[str]:
        """输出当前剪映主页暴露给 UI 自动化的草稿标题，便于排查版本兼容问题。"""
        titles = []

        def walk(control, depth: int = 0) -> None:
            if depth > 6 or len(titles) >= 30:
                return
            try:
                full_desc = str(control.GetPropertyValue(30159) or "")
                if "HomePageDraftTitle" in full_desc:
                    titles.append(full_desc)
                child = control.GetFirstChildControl()
                while child:
                    walk(child, depth + 1)
                    child = child.GetNextSiblingControl()
            except Exception:
                return

        walk(self.app)
        logger.info("visible HomePageDraftTitle controls: %s", titles)
        return titles

    def click_export_button(self) -> None:
        """点击编辑页面的导出按钮
        
        Raises:
            AutomationError: 未找到导出按钮
        """
        export_btn = self.app.TextControl(searchDepth=2, Compare=ControlFinder.desc_matcher("MainWindowTitleBarExportBtn"))
        if not export_btn.Exists(0):
            raise AutomationError("未在编辑窗口中找到导出按钮")
        export_btn.Click(simulateMove=False)
        time.sleep(10)
        self.get_window()

    def get_original_export_path(self) -> str:
        """获取原始导出路径
        
        Returns:
            str: 原始导出路径
            
        Raises:
            AutomationError: 未找到导出路径框
        """
        # 获取原始导出路径（带后缀名）
        export_path_sib = self.app.TextControl(searchDepth=2, Compare=ControlFinder.desc_matcher("ExportPath"))
        if not export_path_sib.Exists(0):
            raise AutomationError("未找到导出路径框")
        export_path_text = export_path_sib.GetSiblingControl(lambda ctrl: True)
        assert export_path_text is not None
        export_path = export_path_text.GetPropertyValue(30159)
        return export_path

    def set_export_resolution(self, resolution: Optional[ExportResolution]) -> None:
        """设置导出分辨率
        
        Args:
            resolution (Optional[ExportResolution]): 导出分辨率，如果为None则不设置
            
        Raises:
            AutomationError: 未找到相关控件
        """
        if resolution is not None:
            setting_group = self.app.GroupControl(searchDepth=1,
                                          Compare=ControlFinder.class_name_matcher("PanelSettingsGroup_QMLTYPE"))
            if not setting_group.Exists(0):
                raise AutomationError("未找到导出设置组")
            resolution_btn = setting_group.TextControl(searchDepth=2, Compare=ControlFinder.desc_matcher("ExportSharpnessInput"))
            if not resolution_btn.Exists(0.5):
                raise AutomationError("未找到导出分辨率下拉框")
            resolution_btn.Click(simulateMove=False)
            time.sleep(0.5)
            resolution_item = self.app.TextControl(
                searchDepth=2, Compare=ControlFinder.desc_matcher(resolution.value)
            )
            if not resolution_item.Exists(0.5):
                raise AutomationError(f"未找到{resolution.value}分辨率选项")
            resolution_item.Click(simulateMove=False)
            time.sleep(0.5)

    def set_export_framerate(self, framerate: Optional[ExportFramerate]) -> None:
        """设置导出帧率
        
        Args:
            framerate (Optional[ExportFramerate]): 导出帧率，如果为None则不设置
            
        Raises:
            AutomationError: 未找到相关控件
        """
        if framerate is not None:
            setting_group = self.app.GroupControl(searchDepth=1,
                                          Compare=ControlFinder.class_name_matcher("PanelSettingsGroup_QMLTYPE"))
            if not setting_group.Exists(0):
                raise AutomationError("未找到导出设置组")
            framerate_btn = setting_group.TextControl(searchDepth=2, Compare=ControlFinder.desc_matcher("FrameRateInput"))
            if not framerate_btn.Exists(0.5):
                raise AutomationError("未找到导出帧率下拉框")
            framerate_btn.Click(simulateMove=False)
            time.sleep(0.5)
            framerate_item = self.app.TextControl(
                searchDepth=2, Compare=ControlFinder.desc_matcher(framerate.value)
            )
            if not framerate_item.Exists(0.5):
                raise AutomationError(f"未找到{framerate.value}帧率选项")
            framerate_item.Click(simulateMove=False)
            time.sleep(0.5)

    def click_final_export_button(self) -> None:
        """点击导出窗口的最终导出按钮
        
        Raises:
            AutomationError: 未找到导出按钮
        """
        export_btn = self.app.TextControl(searchDepth=2, Compare=ControlFinder.desc_matcher("ExportOkBtn", exact=True))
        if not export_btn.Exists(0):
            raise AutomationError("未在导出窗口中找到导出按钮")
        export_btn.Click(simulateMove=False)
        time.sleep(5)

    def __ensure_window_focus(self) -> None:
        """在点击前确保窗口有焦点"""
        # 1. 确保窗口激活
        self.app.SetActive()
        time.sleep(1)
        
        # 2. 确保窗口置顶
        self.app.SetTopmost()
        time.sleep(1)
        
        # 3. 强制获取焦点
        try:
            self.app.SetFocus()
        except:
            pass  # 某些情况下可能失败，但继续执行
        time.sleep(1)

    def wait_for_export_completion(self, timeout: float) -> None:
        """等待导出完成
        
        Args:
            timeout (float): 超时时间（秒）
            
        Raises:
            AutomationError: 导出超时
        """
        # 点击继续导出按钮次数
        continue_export_click_count = 0

        # 等待导出完成
        st = time.time()
        while True:
            self.get_window()
            if self.app_status != "pre_export": break

            succeed_close_btn = self.app.TextControl(searchDepth=2, Compare=ControlFinder.desc_matcher("ExportSucceedCloseBtn"))
            if succeed_close_btn.Exists(0):
                break

            if time.time() - st > timeout:
                raise AutomationError("导出超时, 时限为%d秒" % timeout)

            # 导出过程中，如果出现异常弹窗，则点击继续导出按钮
            if continue_export_click_count < 20:
                print("pyautogui.size(): ", pyautogui.size(), ", click index: ", continue_export_click_count)
                pyautogui.click(x=996, y=597, button="left")
                continue_export_click_count += 1

            time.sleep(1)
        time.sleep(2)

    def return_to_home(self) -> None:
        """回到目录页并稍作延迟"""
        self.get_window()
        self.switch_to_home()
        time.sleep(2)

    def move_exported_file(self, original_path: str, output_path: Optional[str]) -> None:
        """移动导出的文件到指定位置
        
        Args:
            original_path (str): 原始导出路径
            output_path (Optional[str]): 目标输出路径，如果为None则不移动
        """
        logger.info(f"move {original_path} to {output_path}")
        if output_path is not None:
            shutil.move(original_path, output_path)

    def export_draft(self, draft_name: str, output_path: Optional[str] = None, *,
                     resolution: Optional[ExportResolution] = None,
                     framerate: Optional[ExportFramerate] = None,
                     timeout: float = 1200) -> None:
        """导出指定的剪映草稿, **目前仅支持剪映6及以下版本**

        **注意: 需要确认有导出草稿的权限(不使用VIP功能或已开通VIP), 否则可能陷入死循环**

        Args:
            draft_name (`str`): 要导出的剪映草稿名称
            output_path (`str`, optional): 导出路径, 支持指向文件夹或直接指向文件, 不指定则使用剪映默认路径.
            resolution (`Export_resolution`, optional): 导出分辨率, 默认不改变剪映导出窗口中的设置.
            framerate (`Export_framerate`, optional): 导出帧率, 默认不改变剪映导出窗口中的设置.
            timeout (`float`, optional): 导出超时时间(秒), 默认为20分钟.

        Raises:
            `DraftNotFound`: 未找到指定名称的剪映草稿
            `AutomationError`: 剪映操作失败
        """
        logger.info(f"start export {draft_name} to {output_path}")

        # 初始化准备
        self.get_window()
        self.switch_to_home()

        original_path = None

        for i in range(16):
            # 确保窗口有焦点
            self.__ensure_window_focus()
            if self.app_status == "home":
                logger.info("[%d]app is already in home page", i)
                self.find_and_click_draft(draft_name)
            elif self.app_status == "edit":
                logger.info("[%d]app is already in edit page", i)
                # 点击导出按钮进入导出界面
                self.click_export_button()
            elif self.app_status == "pre_export":                
                if self.app_sub_status == "export_start":
                    logger.info("[%d]app is already in pre_export[export_start] page", i)
                    # 获取原始导出路径
                    original_path = self.get_original_export_path()
                    # 设置分辨率（如果指定）
                    self.set_export_resolution(resolution)                    
                    # 设置帧率（如果指定）
                    self.set_export_framerate(framerate)                    
                    # 点击最终导出按钮
                    self.click_final_export_button()
                    # 获取窗口状态
                    self.get_window()
                elif self.app_sub_status == "exporting":
                    logger.info("[%d]app is already in pre_export[exporting] page", i)
                    self.wait_for_export_completion(timeout)                    
                elif self.app_sub_status == "export_succeed":
                    logger.info("[%d]app is already in pre_export[export_succeed] page", i)
                    self.return_to_home()
                    break
                else:
                    raise AutomationError("[%d]app is in unknown sub-status: %s" % (i, self.app_sub_status))
            else:
                raise AutomationError("[%d]app is in unknown status: %s" % (i, self.app_status))
        
        # 移动导出文件到指定路径（如果指定）
        self.move_exported_file(original_path, output_path)
        
        logger.info(f"export {draft_name} to {output_path} completed")

    def switch_to_home(self) -> None:
        """切换到剪映主页"""
        for i in range(8):
            if self.app_status == "home":
                return
            elif self.app_status == "pre_export":
                if self.app_sub_status == "export_succeed":
                    succeed_close_btn = self.app.TextControl(searchDepth=2, Compare=ControlFinder.desc_matcher("ExportSucceedCloseBtn"))
                    if succeed_close_btn.Exists(0):
                        succeed_close_btn.Click(simulateMove=False)
                        time.sleep(2)
                        self.get_window()
            elif self.app_status == "edit":
                close_btn = self.app.GroupControl(searchDepth=1, ClassName="TitleBarButton", foundIndex=3)
                close_btn.Click(simulateMove=False)
                time.sleep(2)
                self.get_window()
            else:
                raise AutomationError("invalid app status: %s" % self.app_status)
        
        logger.info("can not switch to home page after 32 attempts")

    def get_window(self) -> None:
        """寻找剪映窗口并置顶"""
        if hasattr(self, "app") and self.app.Exists(0):
            self.app.SetTopmost(False)

        self.app = uia.WindowControl(searchDepth=1, Compare=self.__jianying_window_cmp)
        if not self.app.Exists(0):
            raise AutomationError("剪映窗口未找到")

        # 寻找可能存在的导出窗口
        export_window = self.app.WindowControl(searchDepth=1, Name="导出")
        if export_window.Exists(0):
            self.app = export_window
            self.app_status = "pre_export"

        # 初始化导出子状态
        self.init_export_sub_status()

        logger.info("app_status: %s, app_sub_status: %s", self.app_status, self.app_sub_status)

        self.app.SetActive()
        self.app.SetTopmost()

    # 初始化导出子状态
    def init_export_sub_status(self) -> None:
        if self.app_status == "pre_export":
            # 0. 初始化默认值为导出中
            self.app_sub_status = "exporting"
            
            # 1. 检查窗口是否停留在导出开始页面
            export_ok_btn = self.app.TextControl(searchDepth=2, Compare=ControlFinder.desc_matcher("ExportOkBtn", exact=True))
            if export_ok_btn.Exists(0):
                self.app_sub_status = "export_start"
                return

            # 2. 检查窗口是否停留在导出完成页面
            succeed_close_btn = self.app.TextControl(searchDepth=2, Compare=ControlFinder.desc_matcher("ExportSucceedCloseBtn"))
            if succeed_close_btn.Exists(0):
                self.app_sub_status = "export_succeed"
                return
        else:
            self.app_sub_status = "none"

    def __jianying_window_cmp(self, control: uia.WindowControl, depth: int) -> bool:
        if control.Name != "剪映专业版":
            return False
        if "HomePage".lower() in control.ClassName.lower():
            self.app_status = "home"
            return True
        if "MainWindow".lower() in control.ClassName.lower():
            self.app_status = "edit"
            return True

        logger.info(f"ClassName: {control.ClassName.lower()}, Name: {control.Name.lower()}")
        return False
