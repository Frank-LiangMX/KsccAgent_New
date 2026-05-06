"""
Agent Worker - Agent 异步执行线程
从 app.py 提取：AgentWorker + TaskWorker
"""

import json
from PyQt6.QtCore import QThread, pyqtSignal


class AgentWorker(QThread):
    text_delta = pyqtSignal(str)
    tool_call = pyqtSignal(str, str)
    tool_result = pyqtSignal(str, bool)
    diff_preview = pyqtSignal(str, str, str)
    confirm_request = pyqtSignal(str, str, str)
    kscc_status = pyqtSignal(str)
    context_info = pyqtSignal(str)
    skill_info = pyqtSignal(str)
    skill_draft = pyqtSignal(str)
    skill_auto_saved = pyqtSignal(str, float)  # skill_id, score
    memory_hits = pyqtSignal(str)  # P3-5: memory hit metadata JSON
    risk_template = pyqtSignal(str)  # P4-5: risk template match JSON
    done = pyqtSignal(str, int, str)
    error = pyqtSignal(str)
    file_modified = pyqtSignal(str, str)

    def __init__(self, agent, prompt, attachments=None):
        super().__init__()
        self.agent = agent
        self.prompt = prompt
        self.attachments = attachments or []
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True
        self.requestInterruption()
        try:
            if getattr(self.agent, "_confirm_event", None):
                self.agent._confirm_result = False
                self.agent._confirm_event.set()
        except Exception:
            pass

    @staticmethod
    def _thinking_status(text: str) -> str:
        t = text.strip().lower()
        if any(k in t for k in ['read', 'file', 'search', 'grep', 'scan', 'look']):
            return "Reading files..."
        if any(k in t for k in ['plan', 'approach', 'step', 'think']):
            return "Planning..."
        if any(k in t for k in ['fix', 'bug', 'error', 'issue']):
            return "Analyzing issue..."
        if any(k in t for k in ['write', 'edit', 'create', 'modify', 'change']):
            return "Preparing changes..."
        if any(k in t for k in ['run', 'execute', 'shell', 'bash', 'test']):
            return "Running commands..."
        return "Thinking..."

    @staticmethod
    def _tool_status(name: str, inp: dict) -> str:
        target = inp.get("file_path", "") or inp.get("path", "") or inp.get("command", "") or inp.get("pattern", "")
        target = str(target)[:40]
        tool_map = {
            "Read": f"Reading: {target}" if target else "Reading file...",
            "Write": f"Writing: {target}" if target else "Writing file...",
            "Edit": f"Editing: {target}" if target else "Editing code...",
            "Bash": f"Running: {target}" if target else "Running command...",
            "Grep": f"Searching: {target}" if target else "Searching code...",
            "Glob": f"Finding: {target}" if target else "Finding files...",
            "WebFetch": "Fetching web content...",
            "WebSearch": "Searching web...",
            "Task": "Running subtask...",
            "AskUserQuestion": "Asking for clarification...",
        }
        return tool_map.get(name, f"Tool: {name}")

    def run(self):
        import asyncio

        async def _r():
            try:
                async for e in self.agent.run(self.prompt, self.attachments):
                    if self._stop_requested or self.isInterruptionRequested():
                        break
                    t = e.get('type', '')
                    if t == 'text_delta':
                        self.text_delta.emit(e.get('text', ''))
                    elif t == 'tool_call':
                        n = e.get('name', '')
                        a = e.get('arguments', {})
                        self.tool_call.emit(n, e.get('preview', ''))
                        if n == 'edit_file' and isinstance(a, dict):
                            self.diff_preview.emit(a.get('path', ''), a.get('old_string', ''), a.get('new_string', ''))
                    elif t == 'thinking':
                        th = e.get('text', '')
                        st = self._thinking_status(th)
                        if st:
                            self.kscc_status.emit(st)
                    elif t == 'kscc_tool':
                        n = e.get('name', '')
                        inp = e.get('input', {})
                        st = self._tool_status(n, inp)
                        if st:
                            self.kscc_status.emit(st)
                    elif t == 'tool_result':
                        self.tool_result.emit(e.get('result', ''), e.get('error', False))
                        if e.get('name', '') in ('write_file', 'edit_file') and not e.get('error'):
                            pass
                    elif t == 'context':
                        self.context_info.emit(json.dumps(e.get('summary', {})))
                    elif t in ('skill_match', 'skill_miss', 'skill_ambiguous', 'skill_status'):
                        self.skill_info.emit(json.dumps(e, ensure_ascii=False))
                    elif t == 'skill_save_draft':
                        self.skill_draft.emit(json.dumps(e.get('draft', {}), ensure_ascii=False))
                    elif t == 'skill_auto_saved':
                        self.skill_auto_saved.emit(e.get('skill_id', ''), e.get('score', 0.0))
                    elif t == 'memory_hits':
                        self.memory_hits.emit(json.dumps(e.get('hits', {}), ensure_ascii=False))
                    elif t == 'risk_template':
                        self.risk_template.emit(json.dumps({
                            'template_id': e.get('template_id', ''),
                            'name': e.get('name', ''),
                            'risk_level': e.get('risk_level', ''),
                            'require_reason': e.get('require_reason', False),
                            'require_confirmation': e.get('require_confirmation', True),
                        }, ensure_ascii=False))
                    elif t == 'confirm':
                        tc = e.get('tool_call', {})
                        a = e.get('args', {})
                        self.confirm_request.emit(a.get('path', ''), a.get('old_string', ''), a.get('new_string', ''))
                        self.agent._confirm_event.clear()
                        self.agent._confirm_event.wait()
                    elif t == 'done':
                        if not (self._stop_requested or self.isInterruptionRequested()):
                            self.done.emit(e.get('text', ''), e.get('turns', 0), json.dumps(e.get('context', {})))
                    elif t == 'error':
                        self.error.emit(e.get('content', ''))
            except Exception as ex:
                self.error.emit(f"{type(ex).__name__}: {ex}")

        try:
            asyncio.run(_r())
        except Exception as ex:
            self.error.emit(f"Worker: {ex}")


class TaskWorker(QThread):
    """任务模式执行线程，基于 TaskExecutor 的状态机驱动"""

    # Agent 兼容信号
    text_delta = pyqtSignal(str)
    tool_call = pyqtSignal(str, str)
    tool_result = pyqtSignal(str, bool)
    diff_preview = pyqtSignal(str, str, str)
    confirm_request = pyqtSignal(str, str, str)
    kscc_status = pyqtSignal(str)
    context_info = pyqtSignal(str)
    skill_info = pyqtSignal(str)
    skill_draft = pyqtSignal(str)
    memory_hits = pyqtSignal(str)  # P3-5
    risk_template = pyqtSignal(str)  # P4-5
    done = pyqtSignal(str, int, str)
    error = pyqtSignal(str)
    file_modified = pyqtSignal(str, str)

    # 任务状态机信号
    task_start = pyqtSignal(str, str)         # task_id, goal
    plan_generated = pyqtSignal(str)           # plan JSON
    step_start = pyqtSignal(str, str)          # step_id, description
    step_complete = pyqtSignal(str, bool, str) # step_id, success, output
    step_failed = pyqtSignal(str, str)         # step_id, error
    step_retry = pyqtSignal(str, int)          # step_id, retry_count
    reflection = pyqtSignal(str, str, str)     # step_id, observation, suggestion
    task_progress = pyqtSignal(str)            # progress JSON
    task_complete = pyqtSignal(str, str)       # task_id, result
    task_failed = pyqtSignal(str, str)         # task_id, error

    def __init__(self, task_executor, prompt, attachments=None):
        super().__init__()
        self.task_executor = task_executor
        self.prompt = prompt
        self.attachments = attachments or []
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True
        self.requestInterruption()

    def run(self):
        import asyncio

        async def _r():
            try:
                async for e in self.task_executor.execute_task(self.prompt, self.attachments):
                    if self._stop_requested or self.isInterruptionRequested():
                        break
                    t = e.get('type', '')

                    # Agent 兼容事件
                    if t == 'text_delta':
                        self.text_delta.emit(e.get('text', ''))
                    elif t == 'tool_call':
                        n = e.get('name', '')
                        self.tool_call.emit(n, e.get('preview', ''))
                        a = e.get('arguments', {})
                        if n == 'edit_file' and isinstance(a, dict):
                            self.diff_preview.emit(a.get('path', ''), a.get('old_string', ''), a.get('new_string', ''))
                    elif t == 'tool_result':
                        self.tool_result.emit(e.get('result', ''), e.get('error', False))

                    # 任务状态机事件
                    elif t == 'task_start':
                        self.task_start.emit(e.get('task_id', ''), e.get('goal', ''))
                        self.kscc_status.emit("Planning...")
                    elif t == 'plan_generated':
                        self.plan_generated.emit(json.dumps(e, ensure_ascii=False))
                        self.kscc_status.emit("Plan ready, executing...")
                    elif t == 'step_start':
                        self.step_start.emit(e.get('step_id', ''), e.get('description', ''))
                        self.kscc_status.emit(f"Step: {e.get('description', '')[:50]}")
                    elif t == 'step_complete':
                        self.step_complete.emit(
                            e.get('step_id', ''), True, e.get('output', '')
                        )
                    elif t == 'step_failed':
                        self.step_failed.emit(e.get('step_id', ''), e.get('error', ''))
                    elif t == 'step_retry':
                        self.step_retry.emit(e.get('step_id', 0), e.get('retry_count', 0))
                        self.kscc_status.emit(f"Retrying step {e.get('step_id', '')}...")
                    elif t == 'step_skipped':
                        self.kscc_status.emit(f"Skipped: {e.get('reason', '')}")
                    elif t == 'reflection':
                        self.reflection.emit(
                            e.get('step_id', ''),
                            e.get('observation', ''),
                            e.get('suggestion', ''),
                        )
                    elif t == 'task_progress':
                        self.task_progress.emit(json.dumps(e.get('progress', {})))
                    elif t == 'memory_hits':
                        self.memory_hits.emit(json.dumps(e.get('hits', {}), ensure_ascii=False))
                    elif t == 'risk_template':
                        self.risk_template.emit(json.dumps({
                            'template_id': e.get('template_id', ''),
                            'name': e.get('name', ''),
                            'risk_level': e.get('risk_level', ''),
                        }, ensure_ascii=False))
                    elif t == 'task_complete':
                        self.task_complete.emit(e.get('task_id', ''), e.get('result', ''))
                        self.kscc_status.emit("")
                        self.done.emit(e.get('result', ''), 0, '{}')
                    elif t == 'task_failed':
                        self.task_failed.emit(e.get('task_id', ''), e.get('error', ''))
                        self.kscc_status.emit("")
                        self.error.emit(e.get('error', 'Task failed'))
                    elif t == 'error':
                        self.error.emit(e.get('content', ''))
            except Exception as ex:
                self.error.emit(f"{type(ex).__name__}: {ex}")

        try:
            asyncio.run(_r())
        except Exception as ex:
            self.error.emit(f"TaskWorker: {ex}")
