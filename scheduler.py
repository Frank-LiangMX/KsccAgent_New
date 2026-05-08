"""
Task Scheduler - 定时任务调度器

QTimer 驱动的后台调度器，定期检查 sche_tasks/ 目录下的任务配置文件，
到了触发时间就通过信号通知 MainWindow 执行任务。

任务配置文件格式 (sche_tasks/*.json):
{
    "name": "整理下载文件夹",
    "schedule": "09:00",
    "repeat": "daily",
    "enabled": true,
    "prompt": "整理 Downloads 文件夹里的新文件，按文件类型分到对应子文件夹里",
    "max_delay_hours": 3,
    "created_at": "2026-05-08T10:00:00",
    "last_run": null,
    "last_status": null
}
"""

from __future__ import annotations

import json
import os
import re
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

logger = logging.getLogger(__name__)

# ── Repeat types ──────────────────────────────────────────────

REPEAT_TYPES = {
    "daily": "每天",
    "weekday": "工作日",
    "weekly": "每周",
    "monthly": "每月",
    "once": "仅一次",
}

# Interval patterns: every_Nh, every_Nm, every_Nd
_INTERVAL_RE = re.compile(r"^every_(\d+)([hmd])$")


def parse_interval_seconds(repeat: str) -> Optional[int]:
    """Parse interval repeat type to seconds. Returns None if not an interval type."""
    m = _INTERVAL_RE.match(repeat)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2)
    if unit == "m":
        return n * 60
    elif unit == "h":
        return n * 3600
    elif unit == "d":
        return n * 86400
    return None


# ── ScheduledTask dataclass ──────────────────────────────────

@dataclass
class ScheduledTask:
    """A single scheduled task configuration."""
    name: str
    schedule: str  # "HH:MM" or interval start time
    repeat: str = "daily"
    enabled: bool = True
    prompt: str = ""
    max_delay_hours: int = 6
    created_at: str = ""
    last_run: Optional[str] = None
    last_status: Optional[str] = None  # "ok" | "failed" | "skipped"
    _file_path: str = field(default="", repr=False)

    @staticmethod
    def from_dict(data: dict, file_path: str = "") -> "ScheduledTask":
        return ScheduledTask(
            name=data.get("name", ""),
            schedule=data.get("schedule", "09:00"),
            repeat=data.get("repeat", "daily"),
            enabled=data.get("enabled", True),
            prompt=data.get("prompt", ""),
            max_delay_hours=data.get("max_delay_hours", 6),
            created_at=data.get("created_at", ""),
            last_run=data.get("last_run"),
            last_status=data.get("last_status"),
            _file_path=file_path,
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("_file_path", None)
        return d


# ── Task Scheduler ────────────────────────────────────────────

class TaskScheduler(QObject):
    """
    QTimer-driven background scheduler.

    定期扫描 sche_tasks/ 目录，检查是否有任务到了触发时间。
    到期的任务通过 task_due 信号通知 MainWindow 执行。
    """

    task_due = pyqtSignal(str, str)  # (task_name, prompt)
    task_skipped = pyqtSignal(str, str)  # (task_name, reason)

    def __init__(self, sche_dir: str, check_interval_ms: int = 30000, parent=None):
        super().__init__(parent)
        self.sche_dir = Path(sche_dir)
        self.done_dir = self.sche_dir / "done"
        self.log_dir = self.sche_dir / "logs"
        self._ensure_dirs()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._check_all_tasks)
        self._check_interval_ms = check_interval_ms
        self._running_tasks: set[str] = set()  # names of currently executing tasks

    def _ensure_dirs(self):
        self.sche_dir.mkdir(parents=True, exist_ok=True)
        self.done_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    # ── Lifecycle ─────────────────────────────────────────────

    def start(self):
        """Start the scheduler timer."""
        logger.info("TaskScheduler started, checking every %dms", self._check_interval_ms)
        self._timer.start(self._check_interval_ms)
        # Do an immediate check
        self._check_all_tasks()

    def stop(self):
        """Stop the scheduler timer."""
        self._timer.stop()
        logger.info("TaskScheduler stopped")

    # ── Task CRUD ─────────────────────────────────────────────

    def list_tasks(self) -> list[ScheduledTask]:
        """List all scheduled tasks from sche_tasks/*.json files."""
        tasks = []
        for f in sorted(self.sche_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                tasks.append(ScheduledTask.from_dict(data, str(f)))
            except Exception as e:
                logger.warning("Failed to load task %s: %s", f.name, e)
        return tasks

    def get_task(self, name: str) -> Optional[ScheduledTask]:
        """Get a task by name."""
        for task in self.list_tasks():
            if task.name == name:
                return task
        return None

    def create_task(self, name: str, schedule: str, prompt: str,
                    repeat: str = "daily", max_delay_hours: int = 6) -> ScheduledTask:
        """Create a new scheduled task."""
        safe_name = re.sub(r'[^\w\u4e00-\u9fff-]', '_', name)[:60]
        file_path = self.sche_dir / f"{safe_name}.json"
        # Avoid collision
        if file_path.exists():
            safe_name = f"{safe_name}_{datetime.now().strftime('%H%M%S')}"
            file_path = self.sche_dir / f"{safe_name}.json"

        task = ScheduledTask(
            name=name,
            schedule=schedule,
            repeat=repeat,
            enabled=True,
            prompt=prompt,
            max_delay_hours=max_delay_hours,
            created_at=datetime.now().isoformat(timespec="seconds"),
            _file_path=str(file_path),
        )
        file_path.write_text(json.dumps(task.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Created scheduled task: %s (%s)", name, file_path.name)
        return task

    def update_task(self, name: str, **kwargs) -> Optional[ScheduledTask]:
        """Update a task's fields. Returns updated task or None if not found."""
        task = self.get_task(name)
        if not task:
            return None
        for key, val in kwargs.items():
            if hasattr(task, key) and not key.startswith("_"):
                setattr(task, key, val)
        file_path = task._file_path or str(self.sche_dir / f"{name}.json")
        Path(file_path).write_text(json.dumps(task.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return task

    def delete_task(self, name: str) -> bool:
        """Delete a scheduled task by name."""
        for f in self.sche_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("name") == name:
                    f.unlink()
                    logger.info("Deleted scheduled task: %s", name)
                    return True
            except Exception:
                pass
        return False

    def set_enabled(self, name: str, enabled: bool) -> bool:
        """Enable or disable a task."""
        task = self.update_task(name, enabled=enabled)
        return task is not None

    def mark_task_run(self, name: str, status: str):
        """Record that a task was just executed."""
        self.update_task(name, last_run=datetime.now().isoformat(timespec="seconds"), last_status=status)
        self._running_tasks.discard(name)

    def mark_task_running(self, name: str):
        """Mark a task as currently running (prevent duplicate triggers)."""
        self._running_tasks.add(name)

    def is_task_running(self, name: str) -> bool:
        return name in self._running_tasks

    # ── Report management ─────────────────────────────────────

    def save_report(self, task_name: str, content: str):
        """Save an execution report to sche_tasks/done/."""
        ts = datetime.now().strftime("%Y-%m-%d_%H%M")
        safe_name = re.sub(r'[^\w\u4e00-\u9fff-]', '_', task_name)[:40]
        report_path = self.done_dir / f"{ts}_{safe_name}.md"
        report_path.write_text(content, encoding="utf-8")
        logger.info("Saved report: %s", report_path.name)

    def get_reports(self, limit: int = 10) -> list[dict]:
        """Get recent execution reports."""
        reports = []
        for f in sorted(self.done_dir.glob("*.md"), reverse=True)[:limit]:
            reports.append({
                "file": f.name,
                "content": f.read_text(encoding="utf-8")[:2000],
                "mtime": datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds"),
            })
        return reports

    # ── Schedule checking ─────────────────────────────────────

    def _check_all_tasks(self):
        """Check all tasks and emit task_due for due ones."""
        now = datetime.now()
        for task in self.list_tasks():
            if not task.enabled:
                continue
            if task.name in self._running_tasks:
                continue
            if self._is_task_due(task, now):
                logger.info("Task due: %s", task.name)
                self._running_tasks.add(task.name)
                self.task_due.emit(task.name, task.prompt)

    def _is_task_due(self, task: ScheduledTask, now: datetime) -> bool:
        """Check if a task is due for execution."""
        repeat = task.repeat

        # Interval-based repeats (every_3h, every_30m, etc.)
        interval_sec = parse_interval_seconds(repeat)
        if interval_sec is not None:
            return self._check_interval_due(task, now, interval_sec)

        # Calendar-based repeats
        return self._check_calendar_due(task, now)

    def _check_interval_due(self, task: ScheduledTask, now: datetime, interval_sec: int) -> bool:
        """Check interval-based task."""
        if task.last_run:
            try:
                last = datetime.fromisoformat(task.last_run)
                elapsed = (now - last).total_seconds()
                if elapsed < interval_sec:
                    return False
            except (ValueError, TypeError):
                pass
        # Check if we're within the schedule window
        return self._in_schedule_window(task.schedule, now, task.max_delay_hours)

    def _check_calendar_due(self, task: ScheduledTask, now: datetime) -> bool:
        """Check calendar-based task (daily, weekday, weekly, monthly, once)."""
        # Parse schedule time "HH:MM"
        try:
            parts = task.schedule.split(":")
            sched_hour = int(parts[0])
            sched_min = int(parts[1]) if len(parts) > 1 else 0
        except (ValueError, IndexError):
            return False

        # Check if already ran today (for non-once tasks)
        if task.last_run and task.repeat != "once":
            try:
                last = datetime.fromisoformat(task.last_run)
                if last.date() == now.date():
                    return False
            except (ValueError, TypeError):
                pass

        # Once: check if already ran ever
        if task.repeat == "once" and task.last_run:
            return False

        # Check day of week/month constraints
        if task.repeat == "weekday" and now.weekday() >= 5:  # Sat=5, Sun=6
            return False
        if task.repeat == "weekly":
            # Run on same day of week as creation day, or Monday if unknown
            try:
                created = datetime.fromisoformat(task.created_at) if task.created_at else now
                if now.weekday() != created.weekday():
                    return False
            except (ValueError, TypeError):
                if now.weekday() != 0:  # Default to Monday
                    return False
        if task.repeat == "monthly":
            try:
                created = datetime.fromisoformat(task.created_at) if task.created_at else now
                if now.day != created.day:
                    return False
            except (ValueError, TypeError):
                if now.day != 1:  # Default to 1st
                    return False

        # Check time window
        sched_time = now.replace(hour=sched_hour, minute=sched_min, second=0, microsecond=0)
        delay = timedelta(hours=task.max_delay_hours)
        return sched_time <= now <= sched_time + delay

    @staticmethod
    def _in_schedule_window(schedule_str: str, now: datetime, max_delay_hours: int) -> bool:
        """Check if current time is within the schedule window for interval tasks."""
        # For interval tasks, schedule_str is just a reference start time "HH:MM"
        try:
            parts = schedule_str.split(":")
            h = int(parts[0])
            m = int(parts[1]) if len(parts) > 1 else 0
        except (ValueError, IndexError):
            return True  # If can't parse, always eligible
        # For interval tasks, we just check elapsed since last_run (done elsewhere)
        # This is a fallback - always allow if no last_run
        return True


# ── Utility functions for tool_executor ───────────────────────

def format_task_list(tasks: list[ScheduledTask]) -> str:
    """Format task list as human-readable text."""
    if not tasks:
        return "当前没有定时任务。"
    lines = ["定时任务列表:\n"]
    for t in tasks:
        status = "✅" if t.enabled else "⏸️"
        last = t.last_run[:16].replace("T", " ") if t.last_run else "从未执行"
        repeat_label = REPEAT_TYPES.get(t.repeat, t.repeat)
        lines.append(f"  {status} [{t.name}] {t.schedule} {repeat_label}")
        lines.append(f"     最近执行: {last} ({t.last_status or '-'})")
        lines.append(f"     指令: {t.prompt[:60]}{'...' if len(t.prompt) > 60 else ''}")
        lines.append("")
    return "\n".join(lines)
