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
                        # thinking 事件不更新状态栏，避免流式 delta 导致状态快速切换
                        # 用户只需要看到工具调用状态，不需要看到 LLM 的内部推理过程
                        pass
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
                        summary = e.get('summary', {})
                        if e.get('warning'):
                            summary['_warning'] = e['warning']
                        if e.get('note'):
                            summary['_note'] = e['note']
                        self.context_info.emit(json.dumps(summary))
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
    skill_auto_saved = pyqtSignal(str, float)  # skill_id, score
    memory_hits = pyqtSignal(str)  # P3-5
    risk_template = pyqtSignal(str)  # P4-5
    done = pyqtSignal(str, int, str)
    error = pyqtSignal(str)
    file_modified = pyqtSignal(str, str)

    # 任务状态机信号
    task_start = pyqtSignal(str, str)         # task_id, goal
    task_resume = pyqtSignal(str, str, dict)  # task_id, goal, resume_info
    plan_generated = pyqtSignal(str)           # plan JSON
    task_progress = pyqtSignal(str)            # progress JSON
    task_complete = pyqtSignal(str, str)       # task_id, result
    task_failed = pyqtSignal(str, str)         # task_id, error

    def __init__(self, task_executor, prompt, attachments=None, resume_state=None):
        super().__init__()
        self.task_executor = task_executor
        self.prompt = prompt
        self.attachments = attachments or []
        self.resume_state = resume_state
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True
        self.requestInterruption()

    def run(self):
        import asyncio

        async def _r():
            try:
                if self.resume_state:
                    gen = self.task_executor.resume_task(self.resume_state)
                else:
                    gen = self.task_executor.execute_task(self.prompt, self.attachments)
                async for e in gen:
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
                    elif t == 'task_resume':
                        self.task_resume.emit(e.get('task_id', ''), e.get('goal', ''), e.get('resume_info', {}))
                        self.kscc_status.emit("Resuming task...")
                    elif t == 'plan_generated':
                        self.plan_generated.emit(json.dumps(e, ensure_ascii=False))
                        self.kscc_status.emit("Plan ready, executing...")
                    elif t == 'task_progress':
                        self.task_progress.emit(json.dumps(e.get('progress', {})))
                    elif t == 'skill_info':
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


_CLASSIFY_PROMPT = """Classify the user task. Reply with exactly one word only.

simple = single question, chat, simple query, brief explanation
complex = multi-step, browser operations, file editing, code generation, refactoring, debugging, planning, research tasks

Reply ONLY: simple or complex"""


class ClassifyWorker(QThread):
    """轻量级分类线程，判断任务是否需要 plan 模式。"""
    finished = pyqtSignal(bool)  # True = complex, False = simple

    def __init__(self, config, prompt: str):
        super().__init__()
        self.config = config
        self.prompt = prompt

    def run(self):
        import asyncio
        try:
            asyncio.run(self._classify())
        except Exception as ex:
            # 分类失败时默认 simple，不阻塞用户
            self.finished.emit(False)

    async def _classify(self):
        from llm_client import create_backend
        from config import get_active_provider

        provider = get_active_provider(self.config)
        backend = create_backend(provider, workspace=self.config.workspace)
        messages = [
            {"role": "system", "content": _CLASSIFY_PROMPT},
            {"role": "user", "content": self.prompt[:2000]},
        ]
        text = ""
        error_msg = ""
        try:
            async for ev in backend.chat(messages, tools=[], max_tokens=100):
                ev_type = ev.get("type", "")
                if ev_type == "text_delta":
                    text += ev.get("text", "")
                elif ev_type == "thinking":
                    # 某些模型（reasoning models）只返回 thinking，不返回 content
                    # 从 thinking 文本中尝试提取分类结果
                    th = ev.get("text", "").strip().lower()
                    if "complex" in th:
                        text = "complex"
                    elif "simple" in th and not text:
                        text = "simple"
                elif ev_type == "error":
                    error_msg = ev.get("content", "")
                    print(f"[ClassifyWorker] API error: {error_msg}")
        except Exception as ex:
            error_msg = str(ex)
            print(f"[ClassifyWorker] exception: {ex}")
        finally:
            close_fn = getattr(backend, "close", None)
            if callable(close_fn):
                close_fn()

        result = text.strip().lower()
        is_complex = "complex" in result
        print(f"[ClassifyWorker] prompt={self.prompt[:80]}... → raw='{text.strip()}' → error='{error_msg}' → is_complex={is_complex}")
        self.finished.emit(is_complex)
