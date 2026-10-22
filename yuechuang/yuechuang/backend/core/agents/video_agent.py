# -*- coding: utf-8 -*-
"""
阶段5: 视频生成智能体
- 从编排器注入的 artifacts["storyboard"] 读取拍摄片段(Segments)
- 视频提示词：风格控制 + 人物列表 + 分镜列表(分镜1: [时长] content...)
- 参考图：从 artifacts["reference_generation"] 读取
- 支持逐项并发生成、实时预览、重新生成
"""

import os
import re
import glob
import hashlib
import asyncio
import logging
import subprocess
from typing import Any, Optional, Dict, List
from concurrent.futures import as_completed
from core.configuration_runtime import ContextThreadPoolExecutor as ThreadPoolExecutor

from .base_agent import AgentInterface
from core.quota import QuotaExceeded
from core.shot_control import ShotPlanError, compile_shot_control, validate_shot_control
from models.video_tudou import TudouVideoClient, VideoInputError
from core.service_settings import SettingsError

logger = logging.getLogger(__name__)
MINIMAX_CONTINUATION_MODEL = "MiniMax-H3-Max-Turbo"
CONTINUATION_BLOCKED_CODE = "CONTINUATION_PREVIOUS_FAILED"
CONTINUATION_BLOCKED_MESSAGE = "上一段生成失败，本段未提交，避免续接失效后白白扣费；可稍后重新生成"
CONTINUATION_NO_END_FRAME_REASON = "续接段只用上一段末帧做首帧，不传尾帧，避免结尾被拉回本段开场构图"
CONTINUATION_FALLBACK_REASON = "未取到上一段末帧，改用本段参考图（首尾帧照旧）"


class VideoDirectorAgent(AgentInterface):
    """视频生成：拍摄片段(Segments) → 组装提示词 → 视频片段"""

    def __init__(self):
        super().__init__(name="VideoDirector")
        self.video_usage_guard = None
        self._episode_by_segment = {}
        self._regenerate_ids = set()
        self._run_failed = set()   # 本轮已尝试但没有产出视频的片段
        self._blocked = {}         # 本轮因上一段失败而未提交的片段 → 错误信息
        self._frames = {}          # 本轮同场景续接段实际用了哪些帧（不含密钥）

    # ─── 版本管理 ───

    @staticmethod
    def _video_base(sid: str) -> str:
        return os.path.join('code/result/video', str(sid))

    def _list_versions(self, sid: str, segment_id: str) -> List[str]:
        """列出某个片段视频的所有历史版本"""
        video_dir = self._video_base(sid)
        pattern = os.path.join(video_dir, f"{segment_id}*.mp4")
        owned = re.compile(rf"^{re.escape(segment_id)}(?:_v\d+|_upload_v\d+)?$")
        files = [f for f in sorted(glob.glob(pattern), key=os.path.getmtime)
                 if owned.fullmatch(os.path.splitext(os.path.basename(f))[0])]
        return files

    def _next_version_path(self, sid: str, segment_id: str) -> str:
        """获取下一个版本路径"""
        video_dir = self._video_base(sid)
        os.makedirs(video_dir, exist_ok=True)

        existing = self._list_versions(sid, segment_id)
        if not existing:
            return os.path.join(video_dir, f"{segment_id}.mp4")

        max_v = 1
        for fp in existing:
            bn = os.path.splitext(os.path.basename(fp))[0]
            m = re.search(r'_v(\d+)$', bn)
            if m:
                max_v = max(max_v, int(m.group(1)))

        return os.path.join(video_dir, f"{segment_id}_v{max_v + 1}.mp4")

    # ─── 视频生成 ───

    def _generate_one(self, sid: str, segment_id: str, *args, **kwargs) -> tuple:
        """生成单个视频片段，返回 (segment_id, path_or_None)。

        没有产出视频的片段记入本轮失败名单。任务在提交时（不是排队时）检查这个名单，
        所以同场景下一段能知道上一段刚刚失败。
        """
        result = (segment_id, None)
        try:
            result = self._generate_clip(sid, segment_id, *args, **kwargs)
            return result
        finally:
            if not result[1]:
                self._run_failed.add(segment_id)

    def _generate_clip(self, sid: str, segment_id: str, prompt: str,
                       img_path: Optional[str], video_model: str,
                       duration: int = 10, sound: str = "",
                       shot_type: str = "multi",
                       video_ratio: str = "16:9",
                       video_resolution: str = "720P",
                       video_generation_mode: str = "first_frame",
                       last_image_path: Optional[str] = None,
                       reference_image_paths: Optional[List[str]] = None,
                       previous_segment: Optional[dict] = None) -> tuple:
        if self.cancellation_check and self.cancellation_check():
            logger.info(f"VideoDirectorAgent: {segment_id} 跳过（用户取消）")
            return segment_id, None

        reference_image_paths = reference_image_paths or []
        if video_generation_mode == "text":
            img_path = None
        elif video_generation_mode == "reference":
            missing_refs = [path for path in reference_image_paths if not path.startswith("https://") and not os.path.exists(path)]
            if not reference_image_paths or missing_refs:
                logger.warning("Reference images missing for %s: %s", segment_id, missing_refs or reference_image_paths)
                return segment_id, None
        elif not img_path or (not img_path.startswith("https://") and not os.path.exists(img_path)):
            logger.warning(f"Image missing for {segment_id}: {img_path}")
            return segment_id, None
        elif previous_segment is not None:
            previous_id = previous_segment.get("segment_id") or ""
            if previous_id in self._run_failed and not self._latest_clip(sid, previous_id):
                # 上一段本轮刚失败：续接已不成立。本段不提交（不扣费），留待重新生成。
                logger.warning("同场景上一段本轮失败，本段未提交: %s", segment_id)
                self._blocked[segment_id] = {
                    "code": CONTINUATION_BLOCKED_CODE,
                    "message": CONTINUATION_BLOCKED_MESSAGE,
                    "previous_segment_id": previous_id,
                }
                return segment_id, None
            frame = self._continuation_frame(sid, previous_segment)
            if frame:
                img_path = frame
                # 只给首帧：尾帧若用本段参考图（多为开场构图），模型会把结尾拉回开场画面（2026-10-12 小样发现）。
                # ToAPIs MiniMax-H3-Max-Turbo 支持“提示词 + 一张 first_frame”的首帧模式。
                last_image_path = None
                self._frames[segment_id] = {
                    "first_frame": "previous_last_frame", "previous_segment_id": previous_id,
                    "end_frame": "none", "reason": CONTINUATION_NO_END_FRAME_REASON,
                }
            else:
                # 上一段有视频但抽帧失败（或上一段不在本轮且没有视频）：保持原行为，改用本段参考图提交。
                logger.info("同场景续接未取到上一段末帧，改用本段参考图: %s", segment_id)
                self._frames[segment_id] = {
                    "first_frame": "own_reference", "previous_segment_id": previous_id,
                    "end_frame": "own_reference" if last_image_path else "none",
                    "reason": CONTINUATION_FALLBACK_REASON,
                }

        save_path = self._next_version_path(sid, segment_id)
        try:
            from models.video_client import VideoClient
            client = VideoClient()
            self._check_cancel()
            if self.video_usage_guard:
                self.video_usage_guard(
                    episode=self._episode_by_segment[segment_id], segment_id=segment_id,
                    model=video_model, duration=duration,
                    regeneration=segment_id in self._regenerate_ids or bool(self._list_versions(sid, segment_id)),
                )
            client.generate_video(
                prompt=prompt,
                image_path=img_path,
                save_path=save_path,
                model=video_model,
                duration=duration,
                sound=sound,
                shot_type=shot_type,
                video_ratio=video_ratio,
                resolution=video_resolution,
                last_image_path=last_image_path if video_generation_mode == "start_end_frame" else None,
                reference_image_paths=reference_image_paths if video_generation_mode == "reference" else None,
            )
            if segment_id in self._frames:
                self._frames[segment_id]["clip"] = save_path
            return segment_id, save_path
        except (QuotaExceeded, SettingsError):
            raise
        except Exception as e:
            logger.error('Video generation failed (%s)', type(e).__name__)
            if os.path.exists(save_path):
                try:
                    os.remove(save_path)
                except Exception:
                    pass
        return segment_id, None

    # ─── 提示词组装 ───

    def _format_shot_section(self, segment: dict) -> str:
        from core import scene_merge
        if segment.get("plan_rule") == scene_merge.PLAN_RULE:
            # Stage 23 multi-shot segment: one line per sub-shot with its time range.
            return scene_merge.format_timeline(segment)
        prompt = "分镜列表："
        shots = segment.get("shots", [])
        for i, shot in enumerate(shots, 1):
            dur = shot.get("duration", 5)
            content = shot.get("content", "").strip()
            prompt += f"\n分镜{i}：[{dur}秒] {content}"
        return prompt

    @staticmethod
    def _extract_shot_section(prompt: str) -> str:
        text = (prompt or "").strip()
        if not text:
            return "分镜列表："
        marker = "分镜列表："
        alt_marker = "分镜列表:"
        if marker in text:
            text = text[text.index(marker):].strip()
        elif alt_marker in text:
            text = marker + text[text.index(alt_marker) + len(alt_marker):].strip()
        else:
            text = f"{marker}\n{text}"
        no_caption_marker = "\n不要生成字幕或水印"
        if no_caption_marker in text:
            text = text[:text.index(no_caption_marker)].rstrip()
        return text

    def _display_prompt(self, segment: dict, video_data: Optional[dict] = None) -> str:
        """前端只展示可编辑的分镜列表；风格和人物信息在调用视频模型前即时拼接。"""
        if video_data and video_data.get("description"):
            return self._extract_shot_section(video_data["description"])
        return self._format_shot_section(segment)

    def _build_character_section(self, segment: dict, character_artifact: Optional[dict]) -> str:
        characters = character_artifact.get("characters", []) if isinstance(character_artifact, dict) else []
        if not characters:
            return "人物列表：无"

        character_map = self._build_name_asset_map(characters)
        selected_assets = []
        seen = set()

        for character_name in segment.get("characters") or []:
            asset = self._match_asset_by_name(str(character_name), characters, character_map)
            asset_key = str(asset.get("id") or asset.get("name") or "") if asset else ""
            if asset and asset_key not in seen:
                selected_assets.append(asset)
                seen.add(asset_key)

        lines = []
        for asset in selected_assets:
            name = str(asset.get("name") or asset.get("id") or "").strip()
            desc = str(asset.get("description") or "").strip()
            if name and desc:
                lines.append(f"- {name}：{desc}")
            elif name:
                lines.append(f"- {name}")

        return "人物列表：\n" + "\n".join(lines) if lines else "人物列表：无"

    def _assemble_prompt(self, segment: dict, style_prompt: str,
                         character_artifact: Optional[dict] = None,
                         video_data: Optional[dict] = None) -> str:
        """组装视频提示词
        格式：
        风格控制：用户选择的风格提示词, 电影质感
        人物列表：从第二阶段人物描述读取
        分镜列表：分镜1:[时长] content... 分镜2:[时长] content...
        """
        if segment.get("video_prompt"):
            shot_section = segment["video_prompt"]
        elif video_data and "description" in video_data:
            # 前端修改后的提示词会存入 artifacts.video_generation.clips.description。
            # 兼容旧格式：保留用户修改过的分镜部分，重新补齐最新风格和人物列表。
            shot_section = self._extract_shot_section(video_data["description"])
        else:
            shot_section = self._format_shot_section(segment)

        return (
            f"风格控制：{style_prompt}\n"
            f"{self._build_character_section(segment, character_artifact)}\n"
            f"{shot_section}\n"
            f"{compile_shot_control(segment) + chr(10) if segment.get('shot_control') else ''}"
            "不要生成字幕或水印"
        )

    def _get_style_keywords(self, session_data: dict) -> str:
        """从会话数据获取风格关键词"""
        style = session_data.get('style', 'realistic').lower()
        
        STYLE_MAP = {
            "anime": "anime style, vibrant colors, clean lines,",
            "realistic": "photorealistic, cinematic lighting, high-detail textures,",
            "cartoon": "cartoon style, thick outlines, bold colors,",
            "3d-animation": "3D CGI animation, stylized feature animation, smooth textures,",
            "oil-painting": "oil painting, artistic brushstrokes, rich textures,",
            "chinese-ink": "Chinese ink wash painting, traditional style, soft strokes,"
        }
        return STYLE_MAP.get(style, "cinematic, high quality,")

    # ─── 参考图获取 ───

    def _get_reference_image(self, sid: str, segment_id: str, scene_map: dict) -> str:
        """获取参考图路径：优先用选中的版本，次之用最新版本"""
        # 1. 检查 session 中 artifacts.reference_generation.scenes 里的 selected
        if segment_id in scene_map and scene_map[segment_id].get("selected"):
            path = scene_map[segment_id]["selected"]
            if path.startswith("https://") or os.path.exists(path):
                return path

        # 2. 回退：扫描磁盘 Scenes 目录
        from .reference_agent import ReferenceGeneratorAgent
        versions = ReferenceGeneratorAgent._list_versions_static(sid, segment_id)
        if versions:
            return versions[-1]

        # 3. 默认路径
        return os.path.abspath(os.path.join('code/result/image', str(sid), 'Scenes', f"{segment_id}.jpg"))

    def _get_next_reference_image(self, sid: str, segment_index: int, segments: list, scene_map: dict) -> Optional[str]:
        """首尾帧模式下，优先用下一个片段参考图作为尾帧。"""
        if segment_index + 1 >= len(segments):
            return None
        next_segment_id = segments[segment_index + 1].get("segment_id")
        if not next_segment_id:
            return None
        path = self._get_reference_image(sid, next_segment_id, scene_map)
        return path if path and (path.startswith("https://") or os.path.exists(path)) else None

    @staticmethod
    def _scene_continuation(model: str, mode: str) -> bool:
        from core.service_settings import raw_model
        return raw_model(model) == MINIMAX_CONTINUATION_MODEL and mode == "start_end_frame"

    @staticmethod
    def _output_resolution(model: str, mode: str, resolution: str) -> str:
        if VideoDirectorAgent._scene_continuation(model, mode) and str(resolution).upper() == "720P":
            return "768P"
        return resolution

    def _same_scene(self, previous: Optional[dict], current: Optional[dict]) -> bool:
        if not previous or not current:
            return False
        prev_ep = previous.get("episode_number", self._episode_by_segment.get(previous.get("segment_id")))
        cur_ep = current.get("episode_number", self._episode_by_segment.get(current.get("segment_id")))
        if prev_ep is not None and cur_ep is not None and prev_ep != cur_ep:
            return False
        left = str(previous.get("location") or "").strip()
        right = str(current.get("location") or "").strip()
        if not left or left != right:
            return False
        prev_key = str(previous.get("scene_key") or "").strip()
        cur_key = str(current.get("scene_key") or "").strip()
        return not (prev_key and cur_key and prev_key != cur_key)

    def _latest_clip(self, sid: str, segment_id: str) -> Optional[str]:
        versions = self._list_versions(sid, segment_id)
        return versions[-1] if versions else None

    def _continuation_frame(self, sid: str, previous: dict) -> Optional[str]:
        video = self._latest_clip(sid, previous.get("segment_id") or "")
        if not video:
            return None
        return self._extract_last_frame(sid, previous["segment_id"], video)

    def _extract_last_frame(self, sid: str, segment_id: str, video_path: str) -> Optional[str]:
        """Decode the previous clip locally. A failed extract never becomes a paid request."""
        if not video_path or not os.path.isfile(video_path):
            return None
        temporary = ""
        try:
            from PIL import Image
            from upload_validation import ffmpeg_executable
            executable = ffmpeg_executable()
            stat = os.stat(video_path)
            token = hashlib.sha256(f"{os.path.abspath(video_path)}:{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()[:16]
            dest_dir = os.path.join(self._video_base(sid), "continuation")
            os.makedirs(dest_dir, exist_ok=True)
            dest = os.path.join(dest_dir, f"{segment_id}_{token}.png")
            if os.path.isfile(dest) and os.path.getsize(dest) > 0:
                return dest
            temporary = dest + ".writing.png"
            counted = subprocess.run(
                [executable, "-nostdin", "-hide_banner", "-protocol_whitelist", "file,pipe",
                 "-i", video_path, "-map", "0:v:0", "-f", "null", "-"],
                capture_output=True, timeout=120, text=True,
            )
            frames = [int(item) for item in re.findall(r"frame=\s*(\d+)", counted.stderr or "")]
            if counted.returncode != 0 or not frames or frames[-1] < 1:
                return None
            extracted = subprocess.run(
                [executable, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                 "-protocol_whitelist", "file,pipe", "-i", video_path,
                 "-vf", f"select=eq(n\\,{frames[-1] - 1})", "-frames:v", "1", temporary],
                capture_output=True, timeout=120,
            )
            if extracted.returncode != 0 or not os.path.isfile(temporary) or os.path.getsize(temporary) <= 0:
                if os.path.exists(temporary):
                    os.remove(temporary)
                return None
            with Image.open(temporary) as image:
                image.verify()
            os.replace(temporary, dest)
            return dest
        except Exception:
            if temporary and os.path.exists(temporary):
                os.remove(temporary)
            logger.info("同场景续接抽帧失败: %s", segment_id)
            return None

    def _clip_request(self, sid: str, segments: list, index: int, segment: dict, scene_map: dict,
                      character_art: dict, mode: str, model: str, resolution: str) -> dict:
        reference_image_paths = None
        previous = None
        img_path = None
        last_img_path = None
        if mode == "reference":
            reference_image_paths = self._get_segment_reference_assets(segment, character_art)
        elif mode in {"first_frame", "start_end_frame"}:
            img_path = self._get_reference_image(sid, segment["segment_id"], scene_map)
            if self._scene_continuation(model, mode):
                last_img_path = img_path
                if index:
                    candidate = segments[index - 1]
                    if self._same_scene(candidate, segment):
                        previous = candidate
            elif mode == "start_end_frame":
                last_img_path = self._get_next_reference_image(sid, index, segments, scene_map)
        return {
            "img_path": img_path,
            "last_img_path": last_img_path,
            "reference_image_paths": reference_image_paths,
            "previous": previous,
            "resolution": self._output_resolution(model, mode, resolution),
        }

    @staticmethod
    def _asset_selected_path(asset: dict) -> str:
        selected = asset.get("selected") or ""
        if selected and (selected.startswith("https://") or os.path.exists(selected)):
            return selected
        # Legacy session compatibility: some old artifacts only have versions and no selected field.
        for path in reversed(asset.get("versions") or []):
            if path and (path.startswith("https://") or os.path.exists(path)):
                return path
        return ""

    @staticmethod
    def _build_name_asset_map(assets: list[dict]) -> dict[str, dict]:
        """Build name -> asset mapping from character_design characters/settings."""
        mapping = {}
        for asset in assets:
            name = str(asset.get("name") or "").strip()
            asset_id = str(asset.get("id") or "").strip()
            if name:
                mapping[name] = asset
            if asset_id:
                mapping[asset_id] = asset
        return mapping

    @staticmethod
    def _match_asset_by_name(name: str, assets: list[dict], asset_map: Optional[dict[str, dict]] = None) -> Optional[dict]:
        clean_name = (name or "").strip()
        if not clean_name:
            return None
        if asset_map and clean_name in asset_map:
            return asset_map[clean_name]
        for asset in assets:
            asset_name = str(asset.get("name") or "").strip()
            if asset_name and asset_name == clean_name:
                return asset
        for asset in assets:
            asset_name = str(asset.get("name") or "").strip()
            if asset_name and (asset_name in clean_name or clean_name in asset_name):
                return asset
        return None

    def _get_segment_reference_assets(self, segment: dict, character_artifact: dict) -> List[str]:
        """参考图生视频：按 segment.characters/location 读取第二阶段用户选中的人物图和场景图。"""
        if segment.get("reference_asset_ids") is not None:
            by_id = {item.get("id"): item for kind in ("characters", "settings", "props")
                     for item in character_artifact.get(kind, [])}
            return list(dict.fromkeys(path for key in segment["reference_asset_ids"]
                                      if (path := self._asset_selected_path(by_id.get(key, {})))))[:10]
        characters = character_artifact.get("characters", []) if isinstance(character_artifact, dict) else []
        settings = character_artifact.get("settings", []) if isinstance(character_artifact, dict) else []
        character_map = self._build_name_asset_map(characters)
        setting_map = self._build_name_asset_map(settings)
        reference_paths: List[str] = []
        seen = set()

        def add_asset(asset: Optional[dict]) -> None:
            if not asset:
                return
            path = self._asset_selected_path(asset)
            if path and path not in seen:
                reference_paths.append(path)
                seen.add(path)

        location = str(segment.get("location") or "").strip()
        add_asset(self._match_asset_by_name(location, settings, setting_map))

        for character_name in segment.get("characters") or []:
            add_asset(self._match_asset_by_name(str(character_name), characters, character_map))

        return reference_paths

    @staticmethod
    def _select_video_model(input_data: dict, session_meta: dict) -> tuple[str, str]:
        mode = (
            input_data.get("video_generation_mode")
            or session_meta.get("video_generation_mode")
            or "first_frame"
        )
        model_key = {
            "first_frame": "video_first_frame_model",
            "start_end_frame": "video_start_end_model",
            "reference": "video_reference_model",
            "text": "video_text_model",
        }.get(mode, "video_first_frame_model")
        model = input_data.get(model_key) or session_meta.get(model_key)
        if not model:
            # Legacy session compatibility: sessions created before mode-specific video models only have video_model.
            model = input_data.get("video_model") or session_meta.get("video_model")
        if not model:
            raise ValueError("Missing required model configuration: video_model")
        return mode, model

    # ─── 预览 / Payload ───

    def _build_preview(self, sid: str, segments: list, scene_map: dict,
                       video_clips: Optional[list] = None) -> list:
        preview = []
        clip_map = {c.get("id"): c for c in (video_clips or []) if isinstance(c, dict) and c.get("id")}
        for idx, seg in enumerate(segments, 1):
            segment_id = seg["segment_id"]
            versions = self._list_versions(sid, segment_id)
            ep_n = seg.get('episode_number', 1)
            seg_n = seg.get('segment_number', idx)
            preview.append({
                "id": segment_id,
                "name": f"第{ep_n}集-片段{seg_n}",
                "episode": ep_n,
                "index": seg_n,
                "description": self._display_prompt(seg, clip_map.get(segment_id)),
                "duration": seg.get('total_duration', 10),
                "selected": versions[-1] if versions else "",
                "versions": versions,
                "status": "done" if versions else "pending",
            })
        return preview

    def _build_payload(self, sid: str, segments: list, video_clips: Optional[list] = None) -> dict:
        clips = []
        clip_map = {c.get("id"): c for c in (video_clips or []) if isinstance(c, dict) and c.get("id")}
        for idx, seg in enumerate(segments, 1):
            segment_id = seg["segment_id"]
            versions = self._list_versions(sid, segment_id)
            ep_n = seg.get('episode_number', 1)
            seg_n = seg.get('segment_number', idx)
            clip = {
                "id": segment_id,
                "name": f"第{ep_n}集-片段{seg_n}",
                "episode": ep_n,
                "index": seg_n,
                "description": self._display_prompt(seg, clip_map.get(segment_id)),
                "duration": seg.get('total_duration', 10),
                "selected": versions[-1] if versions else "",
                "versions": versions,
                "status": "done" if versions else "failed",
            }
            # 未提交的片段状态仍是 failed（后期、重试都按失败处理），另记原因便于查看。
            error = self._blocked.get(segment_id)
            if error is None and not versions and segment_id not in self._run_failed:
                error = (clip_map.get(segment_id) or {}).get("generation_error")
            if isinstance(error, dict):
                clip["generation_error"] = dict(error)
            # 续接段用了哪些帧：只挂在对应的那一版视频上
            frames = self._frames.get(segment_id)
            if not (frames and frames.get("clip") == clip["selected"]):
                frames = (clip_map.get(segment_id) or {}).get("frames")
            if isinstance(frames, dict) and frames.get("clip") and frames.get("clip") == clip["selected"]:
                clip["frames"] = dict(frames)
            clips.append(clip)
        return {
            "payload": {
                "session_id": sid,
                "clips": clips,
            },
            "stage_completed": True,
        }

    def _failed_step(self, segment_id: str) -> str:
        return f"未提交: {segment_id}" if segment_id in self._blocked else f"失败: {segment_id}"

    def _done_progress(self, segment_id: str, path: str, versions: list) -> dict:
        update = {"type": "clips", "id": segment_id, "status": "done", "selected": path, "versions": versions}
        if segment_id in self._frames:
            update["frames"] = dict(self._frames[segment_id])
        return {"asset_complete": update}

    def _failed_progress(self, segment_id: str) -> dict:
        update = {"type": "clips", "id": segment_id, "status": "failed", "selected": "", "versions": []}
        if segment_id in self._blocked:
            update["skipped"] = True
            update["error"] = dict(self._blocked[segment_id])
        elif segment_id in self._frames:
            update["frames"] = dict(self._frames[segment_id])
        return {"asset_complete": update}

    def _update_session_video_data(self, sid: str, segments: list, style_prompt: str) -> None:
        """保留兼容入口；session 状态统一由 WorkflowEngine 持久化。"""
        return

    # ─── 核心流程 ───

    async def process(self, input_data: Any, intervention: Optional[Dict] = None) -> Dict:
        
        input_data = self._merge_session_params(input_data)
        sid = input_data["session_id"]
        
        # ═══ 介入：用户选择指定版本 ═══
        if intervention and "selected_clips" in intervention:
            selected_clips = intervention["selected_clips"] # Dict[segment_id, path]
            logger.info("[VideoAgent] 用户更新片段选择")

            # 返回当前状态
            artifacts = self._session_artifacts(input_data)
            episodes = artifacts.get('storyboard', {}).get('episodes', [])
            segments = []
            for ep in episodes:
                segments.extend(ep.get("segments", []))
            video_clips = artifacts.get('video_generation', {}).get('clips', [])
            payload = self._build_payload(sid, segments, video_clips)
            for clip in payload.get("payload", {}).get("clips", []):
                clip_id = clip.get("id")
                if clip_id in selected_clips:
                    clip["selected"] = selected_clips[clip_id]
            return payload

        session_meta = self._session_meta(input_data)
        video_generation_mode, video_model = self._select_video_model(input_data, session_meta)
        pilot_mode = bool(input_data.get("pilot_mode"))
        controlled_episode = bool(input_data.get("controlled_episode"))
        cost_controlled = pilot_mode or controlled_episode
        segment_limit = 4 if controlled_episode else 1
        enable_concurrency = False if cost_controlled else input_data.get("enable_concurrency", True)
        from models.config_model import get_max_concurrency
        concurrency = get_max_concurrency(video_model, enable_concurrency)
        if self._scene_continuation(video_model, video_generation_mode):
            concurrency = 1
        
        video_ratio = input_data.get("video_ratio", "16:9")
        video_resolution = input_data.get("video_resolution", "720P")
        video_sound = "on"
        video_shot_type = "multi"

        artifacts = self._session_artifacts(input_data)
        
        # 1. 获取拍摄片段列表 (从 Storyboard)
        episodes = artifacts.get('storyboard', {}).get('episodes', [])
        segments = []
        for ep in episodes:
            segments.extend(ep.get("segments", []))
        if controlled_episode and len(segments) != 4:
            raise ShotPlanError("受控整集必须恰好四个片段，请重新确认分镜；不会自动截取前四段")
        if pilot_mode and not controlled_episode:
            segments = segments[:segment_limit]
        if not segments:
            raise Exception("未找到分镜片段数据，请先完成阶段3")
        
        self._episode_by_segment = {
            seg['segment_id']: ep.get('episode_number', 1)
            for ep in episodes for seg in ep.get('segments', [])
        }
        self._regenerate_ids = set((intervention or {}).get('regenerate_clips') or [])
        self._run_failed = set()
        self._blocked = {}
        self._frames = {}
        video_clips = artifacts.get('video_generation', {}).get('clips', [])

        # 2. 获取参考图路径映射 (从 Reference Generation)
        ref_art = artifacts.get('reference_generation', {})
        scene_list = ref_art.get('scenes', [])
        scene_map = {s['id']: s for s in scene_list if 'id' in s}
        character_art = artifacts.get('character_design', {})
        
        style_zh = input_data.get('style') or session_meta.get('style') or 'realistic'
        style_prompt = self._get_style_prompt(style_zh)

        # Validate the WHOLE pending batch before any worker/paid request starts.
        for index, segment in enumerate(segments):
            seg_id = segment["segment_id"]
            if self._regenerate_ids:
                if seg_id not in self._regenerate_ids:
                    continue
            elif self._list_versions(sid, seg_id):
                continue
            if controlled_episode and not segment.get("shot_control"):
                raise ShotPlanError("旧版受控分镜缺少对白时间表，请重新生成并确认分镜后再生成视频")
            duration = 15 if controlled_episode else 6 if pilot_mode else segment.get("total_duration", 10)
            if segment.get("shot_control"):
                validate_shot_control({**segment, "total_duration": duration})
            from core.service_settings import raw_model, validate_video_options
            request = self._clip_request(sid, segments, index, segment, scene_map, character_art, video_generation_mode, video_model, video_resolution)
            validate_video_options(video_model, ratio=video_ratio, resolution=request["resolution"], duration=duration, mode=video_generation_mode)
            if raw_model(video_model) in {"grok-imagine-video-1.5", "grok-video-1.5", "MiniMax-H3-Max-Turbo"}:
                refs = request["reference_image_paths"] if video_generation_mode == "reference" else None
                first = request["img_path"] if video_generation_mode in {"first_frame", "start_end_frame"} else None
                last = request["last_img_path"] if video_generation_mode == "start_end_frame" else None
                if (video_generation_mode == "reference" and not refs) or (video_generation_mode in {"first_frame", "start_end_frame"} and not first) or (video_generation_mode == "start_end_frame" and not last):
                    raise VideoInputError("所选视频模式缺少参考图片，请补齐素材；不会自动回退为文生视频或首帧模式")
                if raw_model(video_model) == "grok-imagine-video-1.5":
                    TudouVideoClient.build_request("preflight", first, raw_model(video_model), duration, video_ratio, request["resolution"], last, refs)
                else:
                    from models.media_toapis import video_request
                    video_request("preflight", first, raw_model(video_model), duration, video_ratio, request["resolution"], last, refs)

        # ═══ 介入：重新生成指定片段 ═══
        if intervention:
            regen_ids = intervention.get("regenerate_clips", [])
            if cost_controlled and regen_ids:
                raise ValueError("受控试跑不允许重新生成视频，以确保不超出一次视频任务上限")
            if regen_ids:
                self._report_progress("视频生成", "重新生成中...", 5)
                segment_map = {s['segment_id']: s for s in segments}
                clip_map = {c['id']: c for c in video_clips}
                if self._scene_continuation(video_model, video_generation_mode):
                    order = {segment["segment_id"]: index for index, segment in enumerate(segments)}
                    regen_ids = sorted(regen_ids, key=lambda item: order.get(item, 10**9))
                
                def regen_run():
                    quota_error = None
                    done = 0
                    with ThreadPoolExecutor(max_workers=concurrency) as executor:
                        futs = {}
                        for seg_id in regen_ids:
                            seg = segment_map.get(seg_id)
                            clip = clip_map.get(seg_id) if clip_map.get(seg_id) else None
                            if not seg: continue
                            prompt = self._assemble_prompt(seg, style_prompt, character_art, video_data=clip)
                            seg_index = segments.index(seg)
                            request = self._clip_request(sid, segments, seg_index, seg, scene_map, character_art, video_generation_mode, video_model, video_resolution)
                            img_path = request["img_path"]
                            reference_image_paths = request["reference_image_paths"]
                            last_img_path = request["last_img_path"]
                            if video_generation_mode == "reference" and not reference_image_paths:
                                logger.warning("VideoDirectorAgent: %s 参考图模式未匹配到第二阶段人物/场景图", seg_id)
                            if video_generation_mode == "start_end_frame" and not last_img_path:
                                logger.warning("VideoDirectorAgent: %s 首尾帧模式缺少尾帧，回退为首帧生视频入参", seg_id)
                            duration = 15 if controlled_episode else 6 if pilot_mode else seg.get("total_duration", 10)
                            existing_versions = self._list_versions(sid, seg_id)
                            self._report_progress("视频生成", f"启动生成: {seg_id}", 5, data={
                                "asset_complete": {
                                    "type": "clips", "id": seg_id,
                                    "status": "running",
                                    "versions": existing_versions,
                                }
                            })
                            fut = executor.submit(
                                self._generate_one, sid, seg_id, prompt,
                                img_path, video_model, duration,
                                video_sound, video_shot_type, video_ratio, request["resolution"],
                                video_generation_mode, last_img_path, reference_image_paths, request["previous"]
                            )
                            futs[fut] = seg_id
                        for fut in as_completed(futs):
                            sid_done = futs[fut]
                            try:
                                _, res_path = fut.result()
                            except QuotaExceeded as exc:
                                quota_error = exc
                                res_path = None
                            except Exception as e:
                                logger.error('Video regeneration worker failed (%s)', type(e).__name__)
                                res_path = None
                            done += 1
                            pct = 5 + int(90 * done / max(1, len(regen_ids)))
                            if res_path:
                                versions = self._list_versions(sid, sid_done)
                                self._report_progress("视频生成", f"完成: {sid_done}", pct,
                                                      data=self._done_progress(sid_done, res_path, versions))
                            else:
                                self._report_progress("视频生成", self._failed_step(sid_done), pct,
                                                      data=self._failed_progress(sid_done))
                    if quota_error:
                        raise quota_error
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, regen_run)
                
                # 同步到 session artifacts
                self._update_session_video_data(sid, segments, style_prompt)
                
                return self._build_payload(sid, segments, video_clips)

        # ═══ 正常流程：全量生成 ═══
        self._report_progress("视频生成", "正在准备数据...", 2)
        preview = self._build_preview(sid, segments, scene_map, video_clips)
        self._report_progress("视频生成", "加载视频列表", 5, data={"assets_preview": {"clips": preview}})

        def run():
            quota_error = None
            tasks = []
            for seg_index, seg in enumerate(segments):
                seg_id = seg["segment_id"]
                existing = self._list_versions(sid, seg_id)
                if existing: continue
                prompt = self._assemble_prompt(seg, style_prompt, character_art)
                request = self._clip_request(sid, segments, seg_index, seg, scene_map, character_art, video_generation_mode, video_model, video_resolution)
                if video_generation_mode == "reference" and not request["reference_image_paths"]:
                    logger.warning("VideoDirectorAgent: %s 参考图模式未匹配到第二阶段人物/场景图", seg_id)
                if video_generation_mode == "start_end_frame" and not request["last_img_path"]:
                    logger.warning("VideoDirectorAgent: %s 首尾帧模式缺少尾帧，回退为首帧生视频入参", seg_id)
                duration = 15 if controlled_episode else 6 if pilot_mode else seg.get("total_duration", 10)
                tasks.append((seg_id, prompt, request, duration))
            if cost_controlled:
                tasks = tasks[:segment_limit]
            if not tasks:
                self._report_progress("视频生成", "所有视频片段已存在", 95)
                return
            done = 0
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                futs = {}
                for seg_id, prompt, request, dur in tasks:
                    # 提交前立即发送正在运行的状态，让前端 UI 更新
                    self._report_progress("视频生成", f"启动生成: {seg_id}", 5, data={
                        "asset_complete": {
                            "type": "clips", "id": seg_id,
                            "status": "running"
                        }
                    })
                    fut = executor.submit(
                        self._generate_one, sid, seg_id, prompt,
                        request["img_path"], video_model, dur,
                        video_sound, video_shot_type, video_ratio, request["resolution"],
                        video_generation_mode, request["last_img_path"], request["reference_image_paths"], request["previous"]
                    )
                    futs[fut] = seg_id
                for fut in as_completed(futs):
                    sid_done = futs[fut]
                    try:
                        _, res_path = fut.result()
                    except QuotaExceeded as exc:
                        quota_error = exc
                        res_path = None
                    except Exception as e:
                        logger.error('Video worker failed (%s)', type(e).__name__)
                        res_path = None
                    done += 1
                    pct = 5 + int(90 * done / max(1, len(tasks)))
                    if res_path:
                        versions = self._list_versions(sid, sid_done)
                        self._report_progress("视频生成", f"完成: {sid_done}", pct,
                                              data=self._done_progress(sid_done, res_path, versions))
                    else:
                        self._report_progress("视频生成", self._failed_step(sid_done), pct,
                                              data=self._failed_progress(sid_done))
                    if self.cancellation_check and self.cancellation_check():
                        for f in futs:
                            if not f.done(): f.cancel()
                        break
            if quota_error:
                raise quota_error
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, run)
        
        # 同步到 session artifacts
        self._update_session_video_data(sid, segments, style_prompt)
        
        self._report_progress("视频生成", "完成", 100)
        return self._build_payload(sid, segments, video_clips)
