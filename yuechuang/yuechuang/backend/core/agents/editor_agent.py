"""阶段 6：按分镜顺序拼接本地片段，保留历史成片。"""

import asyncio
import logging
import os
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from core.workflow_rules import WorkflowConflict
from upload_validation import ffmpeg_executable

from .base_agent import AgentInterface


class VideoEditorAgent(AgentInterface):
    def __init__(self):
        super().__init__(name="VideoEditor")

    def _run_ffmpeg(self, command, timeout=600):
        """Bound runtime and reap FFmpeg on cancellation, timeout and failure."""
        self._check_cancel()
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                self._check_cancel()
                if time.monotonic() >= deadline:
                    raise WorkflowConflict('EXPORT_TIMEOUT', '成片导出超时，请减少片段数量后重试')
                time.sleep(0.1)
            self._check_cancel()
            if process.returncode:
                logging.getLogger(__name__).warning("FFmpeg failed with exit code %s", process.returncode)
                raise WorkflowConflict('EXPORT_FAILED', '视频拼接失败，请检查片段格式后重试')
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

    async def process(self, input_data, intervention=None):
        input_data = self._merge_session_params(input_data)
        sid = input_data['session_id']
        artifacts = self._session_artifacts(input_data)
        clips = artifacts.get('video_generation', {}).get('clips', [])
        episodes = artifacts.get('storyboard', {}).get('episodes', [])
        titles = {int(ep['episode_number']): ep.get('act_title', '') for ep in episodes}
        ordered = []
        selection = (intervention or {}).get('clip_ids')
        if selection is not None:
            if not isinstance(selection, list) or not selection or any(not isinstance(key, str) for key in selection) or len(set(selection)) != len(selection):
                raise WorkflowConflict('EXPORT_SELECTION_INVALID', '请选择不重复的视频片段')
            by_id = {clip['id']: clip for clip in clips}
            episode_by_id = {seg['segment_id']: int(ep['episode_number']) for ep in episodes for seg in ep.get('segments', [])}
            if any(key not in by_id for key in selection):
                raise WorkflowConflict('EXPORT_SELECTION_INVALID', '所选视频片段不存在，请刷新')
            ordered = [(episode_by_id.get(key, by_id[key].get('episode', 1)), by_id[key].get('selected')) for key in selection]
        elif episodes:
            by_id = {clip['id']: clip for clip in clips}
            for ep in episodes:
                for seg in ep.get('segments', []):
                    clip = by_id.get(seg['segment_id'], {})
                    ordered.append((int(ep['episode_number']), clip.get('selected')))
        elif clips:
            for clip in sorted(clips, key=lambda c: c.get('index', 0)):
                match = re.search(r'(\d+)_\d+', clip.get('id', ''))
                episode = clip.get('episode') or (int(match.group(1)) if match else 1)
                ordered.append((int(episode), clip.get('selected')))
        else:
            selected = input_data.get('selected_clips', {})
            for key in sorted(selected, key=lambda k: tuple(map(int, re.findall(r'\d+', k))) or (999,)):
                ordered.append((1, selected[key]))
        if not ordered or any(not path or not Path(path).is_file() for _, path in ordered):
            raise WorkflowConflict('EXPORT_INPUT_MISSING', '部分视频片段缺失，请返回视频阶段补齐后重试')
        # Concat files cannot safely represent line breaks; reject rather than silently omit.
        if any('\n' in path or '\r' in path for _, path in ordered):
            raise WorkflowConflict('EXPORT_INPUT_INVALID', '视频路径无效，请重新上传该片段')
        executable = ffmpeg_executable()
        groups = {}
        for ep, path in ordered:
            groups.setdefault(ep, []).append(Path(path).resolve())

        def run():
            root = Path('code/result/video') / sid / 'output'
            root.mkdir(parents=True, exist_ok=True)
            published = []
            try:
                with tempfile.TemporaryDirectory(prefix='.export-', dir=root) as temp:
                    results = []
                    for index, (ep, paths) in enumerate(groups.items()):
                        self._check_cancel()
                        self._report_progress('后期制作', f'正在拼接第 {ep} 集', 10 + index / len(groups) * 80)
                        # Provider MP4s can carry cover-art video streams and
                        # different time bases. Decode each clip independently
                        # before using the concat demuxer; otherwise later PTS
                        # values can become hours of frozen frames.
                        normalized = []
                        for clip_index, path in enumerate(paths):
                            part = Path(temp) / f'{ep}-part-{clip_index}.mp4'
                            self._run_ffmpeg([
                                executable, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                                '-protocol_whitelist', 'file,pipe', '-i', str(path),
                                '-map', '0:v:0', '-map', '0:a:0?', '-map_metadata', '-1',
                                '-fps_mode', 'passthrough',
                                '-c:v', 'libx264', '-preset', 'fast', '-crf', '20',
                                '-pix_fmt', 'yuv420p', '-video_track_timescale', '90000',
                                '-c:a', 'aac', '-ar', '48000', '-ac', '2', str(part),
                            ])
                            normalized.append(part.resolve())
                        manifest = Path(temp) / f'{ep}.txt'
                        manifest.write_text(''.join("file '" + str(path).replace("'", "'\\''") + "'\n" for path in normalized))
                        draft = Path(temp) / f'{ep}.mp4'
                        self._run_ffmpeg([
                            executable, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                            '-f', 'concat', '-safe', '0', '-protocol_whitelist', 'file,pipe', '-i', str(manifest),
                            '-fps_mode', 'passthrough',
                            '-c:v', 'libx264', '-preset', 'fast', '-crf', '22', '-c:a', 'aac',
                            '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(draft),
                        ])
                        if not draft.is_file() or draft.stat().st_size == 0:
                            raise WorkflowConflict('EXPORT_FAILED', '未生成有效成片，请重试')
                        output = root / f'{sid}_ep{ep}_{uuid.uuid4().hex[:12]}.mp4'
                        results.append({'episode': ep, 'path': str(output), 'name': titles.get(ep) or f'第 {ep} 集'})
                    self._check_cancel()
                    for result in results:
                        output = Path(result['path'])
                        os.replace(Path(temp) / f"{result['episode']}.mp4", output)
                        published.append(output)
                    return results
            except BaseException:
                for output in published:
                    output.unlink(missing_ok=True)
                raise

        results = await asyncio.to_thread(run)
        self._report_progress('后期制作', '成片完成，等待确认', 100)
        history = artifacts.get('post_production', {}).get('export_history', [])
        previous = artifacts.get('post_production', {}).get('final_videos', [])
        return {'payload': {'session_id': sid, 'final_videos': results, 'final_video': results[0]['path'],
                            'export_history': history + previous, 'clip_ids': selection},
                'stage_completed': True, 'requires_intervention': True}
