"""Local Demo video dispatch budgets; durable reservations, not provider invoices."""

import math
import os
from datetime import datetime
from zoneinfo import ZoneInfo


class QuotaExceeded(ValueError):
    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(message)


def _limit(name):
    raw = os.getenv(name, "-1")
    try:
        value = int(raw)
        if value < -1:
            raise ValueError
    except ValueError:
        raise ValueError(f"{name} 必须为 -1（关闭）或非负整数") from None
    return None if value == -1 else value


def reservation_seconds(model, duration):
    """Conservative seconds before adapter clamping; excludes SDK/HTTP retries."""
    if isinstance(duration, bool) or not isinstance(duration, (int, float)):
        raise ValueError("视频时长必须是正数")
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("视频时长必须是正数")
    if str(model).startswith("agnes"):
        return max(4, min(12, int(duration)))
    # Kling can round short requests up. Reserve at least 10 for other adapters.
    return max(10, math.ceil(duration))


class VideoQuota:
    def __init__(self, store, daily_seconds=None, max_regenerations=None, day=None):
        self.store = store
        self.daily_seconds = daily_seconds
        self.max_regenerations = max_regenerations
        self._day = day or (lambda: datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat())

    @classmethod
    def from_env(cls, store):
        return cls(store, _limit("YC_DAILY_VIDEO_SECONDS"),
                   _limit("YC_MAX_VIDEO_REGENERATIONS_PER_EPISODE"))

    def reserve(self, task_id, session_id, episode, segment_id, model, duration, regeneration=False):
        """Atomic check + reservation immediately before a possibly billed call.

        Never refund an uncertain provider outcome. Rows have no session FK, so
        deleting a project cannot restore this backend's shared daily budget.
        """
        if isinstance(episode, bool) or not isinstance(episode, int) or episode < 1:
            raise ValueError("无法确定视频所属集数")
        seconds, day = reservation_seconds(model, duration), self._day()
        with self.store._lock, self.store.db:
            db = self.store.db
            if db.execute("SELECT 1 FROM video_usage WHERE task_id=? AND segment_id=?",
                          (task_id, segment_id)).fetchone():
                raise QuotaExceeded("VIDEO_ALREADY_DISPATCHED", "该任务的片段已发起生成，请查询任务状态")
            used = db.execute("SELECT COALESCE(SUM(seconds), 0) FROM video_usage WHERE day=?", (day,)).fetchone()[0]
            if self.daily_seconds is not None and used + seconds > self.daily_seconds:
                raise QuotaExceeded("VIDEO_QUOTA_EXCEEDED", "今日共享视频生成额度不足，请调整额度或次日重试")
            prior_segment = db.execute(
                "SELECT 1 FROM video_usage WHERE session_id=? AND segment_id=? LIMIT 1",
                (session_id, segment_id)).fetchone()
            same_round = db.execute(
                "SELECT 1 FROM video_usage WHERE task_id=? AND episode=? AND regeneration=1",
                (task_id, episode)).fetchone()
            new_round = bool((regeneration or prior_segment) and not same_round)
            if new_round:
                count = db.execute(
                    "SELECT COALESCE(SUM(regeneration), 0) FROM video_usage WHERE session_id=? AND episode=?",
                    (session_id, episode)).fetchone()[0]
                if self.max_regenerations is not None and count >= self.max_regenerations:
                    raise QuotaExceeded("VIDEO_REGENERATION_LIMIT", "本集视频重生成次数已达上限，请调整配置后重试")
            db.execute("""INSERT INTO video_usage
                (task_id, session_id, episode, segment_id, day, seconds, regeneration)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (task_id, session_id, episode, segment_id, day, seconds, int(new_round)))

    def snapshot(self, session_id):
        day = self._day()
        with self.store._lock:
            used = self.store.db.execute(
                "SELECT COALESCE(SUM(seconds), 0) FROM video_usage WHERE day=?", (day,)).fetchone()[0]
            rows = self.store.db.execute("""SELECT episode, SUM(regeneration) FROM video_usage
                WHERE session_id=? GROUP BY episode ORDER BY episode""", (session_id,)).fetchall()
        return {
            "enabled": self.daily_seconds is not None or self.max_regenerations is not None,
            "scope": "shared_backend", "day": day, "timezone": "Asia/Shanghai",
            "daily_video_seconds_limit": self.daily_seconds,
            "reserved_video_seconds": used,
            "remaining_video_seconds": None if self.daily_seconds is None else max(0, self.daily_seconds - used),
            "max_video_regenerations_per_episode": self.max_regenerations,
            "episode_regenerations": {str(ep): count for ep, count in rows},
            "accounting": "dispatch_reservations_not_billed_usage",
        }
