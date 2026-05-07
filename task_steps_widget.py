"""
Task Steps Widget - 任务步骤视图

显示任务执行步骤的状态和进度
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar,
    QFrame, QScrollArea, QSizePolicy, QToolButton,
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QColor, QPalette

from task_state import TaskState, Step, StepStatus, TaskPhase


class StepStatusIcon(QWidget):
    """步骤状态图标"""

    def __init__(self, status: StepStatus = StepStatus.PENDING, parent=None):
        super().__init__(parent)
        self._status = status
        self.setFixedSize(24, 24)

    def set_status(self, status: StepStatus):
        self._status = status
        self.update()

    def paintEvent(self, event):
        from PyQt6.QtGui import QPainter, QPen, QBrush
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 根据状态选择颜色和图标
        if self._status == StepStatus.PENDING:
            color = QColor("#9CA3AF")  # 灰色
            self._draw_circle(painter, color)
        elif self._status == StepStatus.RUNNING:
            color = QColor("#3B82F6")  # 蓝色
            self._draw_circle(painter, color, filled=True)
        elif self._status == StepStatus.SUCCESS:
            color = QColor("#10B981")  # 绿色
            self._draw_checkmark(painter, color)
        elif self._status == StepStatus.FAILED:
            color = QColor("#EF4444")  # 红色
            self._draw_x_mark(painter, color)
        elif self._status == StepStatus.RETRYING:
            color = QColor("#F59E0B")  # 黄色
            self._draw_circle(painter, color, filled=True)
        elif self._status == StepStatus.SKIPPED:
            color = QColor("#6B7280")  # 深灰色
            self._draw_dash(painter, color)

        painter.end()

    def _draw_circle(self, painter, color, filled=False):
        from PyQt6.QtGui import QPen, QBrush
        pen = QPen(color, 2)
        painter.setPen(pen)
        if filled:
            painter.setBrush(QBrush(color))
        else:
            painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        painter.drawEllipse(4, 4, 16, 16)

    def _draw_checkmark(self, painter, color):
        from PyQt6.QtGui import QPen
        pen = QPen(color, 2)
        painter.setPen(pen)
        painter.drawLine(6, 12, 10, 16)
        painter.drawLine(10, 16, 18, 8)

    def _draw_x_mark(self, painter, color):
        from PyQt6.QtGui import QPen
        pen = QPen(color, 2)
        painter.setPen(pen)
        painter.drawLine(6, 6, 18, 18)
        painter.drawLine(6, 18, 18, 6)

    def _draw_dash(self, painter, color):
        from PyQt6.QtGui import QPen
        pen = QPen(color, 2)
        painter.setPen(pen)
        painter.drawLine(6, 12, 18, 12)


class StepWidget(QFrame):
    """单个步骤的显示组件"""

    clicked = pyqtSignal(str)  # step_id

    def __init__(self, step: Step, parent=None):
        super().__init__(parent)
        self._step = step
        self._setup_ui()
        self._update_style()

    def _setup_ui(self):
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Raised)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)

        # 状态图标
        self._status_icon = StepStatusIcon(self._step.status)
        layout.addWidget(self._status_icon)

        # 步骤信息
        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)

        # 步骤 ID 和描述
        self._id_label = QLabel(self._step.id)
        self._id_label.setFont(QFont("", 9, QFont.Weight.Bold))
        info_layout.addWidget(self._id_label)

        self._desc_label = QLabel(self._step.description)
        self._desc_label.setWordWrap(True)
        info_layout.addWidget(self._desc_label)

        # 状态和耗时
        status_layout = QHBoxLayout()
        status_layout.setSpacing(8)

        self._status_label = QLabel(self._step.status.value.title())
        self._status_label.setFont(QFont("", 8))
        status_layout.addWidget(self._status_label)

        if self._step.result and self._step.result.duration_ms > 0:
            self._time_label = QLabel(f"{self._step.result.duration_ms}ms")
            self._time_label.setFont(QFont("", 8))
            status_layout.addWidget(self._time_label)

        status_layout.addStretch()
        info_layout.addLayout(status_layout)

        layout.addLayout(info_layout, 1)

        # 重试次数（如果有）
        if self._step.retry_count > 0:
            retry_label = QLabel(f"Retry {self._step.retry_count}/{self._step.max_retries}")
            retry_label.setFont(QFont("", 8))
            retry_label.setStyleSheet("color: #F59E0B;")
            layout.addWidget(retry_label)

    def _update_style(self):
        """根据状态更新样式"""
        if self._step.status == StepStatus.RUNNING:
            self.setStyleSheet("""
                StepWidget {
                    background-color: #EFF6FF;
                    border: 1px solid #BFDBFE;
                    border-radius: 4px;
                }
            """)
        elif self._step.status == StepStatus.SUCCESS:
            self.setStyleSheet("""
                StepWidget {
                    background-color: #F0FDF4;
                    border: 1px solid #BBF7D0;
                    border-radius: 4px;
                }
            """)
        elif self._step.status == StepStatus.FAILED:
            self.setStyleSheet("""
                StepWidget {
                    background-color: #FEF2F2;
                    border: 1px solid #FECACA;
                    border-radius: 4px;
                }
            """)
        elif self._step.status == StepStatus.RETRYING:
            self.setStyleSheet("""
                StepWidget {
                    background-color: #FFFBEB;
                    border: 1px solid #FDE68A;
                    border-radius: 4px;
                }
            """)
        else:
            self.setStyleSheet("""
                StepWidget {
                    background-color: #F9FAFB;
                    border: 1px solid #E5E7EB;
                    border-radius: 4px;
                }
            """)

    def update_step(self, step: Step):
        """更新步骤状态"""
        self._step = step
        self._status_icon.set_status(step.status)
        self._status_label.setText(step.status.value.title())
        self._update_style()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._step.id)
        super().mousePressEvent(event)


class TaskProgressWidget(QWidget):
    """任务进度显示组件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
        self._task_state: TaskState = None

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # 任务标题
        self._title_label = QLabel("Task Progress")
        self._title_label.setFont(QFont("", 12, QFont.Weight.Bold))
        layout.addWidget(self._title_label)

        # 进度条
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setTextVisible(True)
        layout.addWidget(self._progress_bar)

        # 状态信息
        status_layout = QHBoxLayout()
        status_layout.setSpacing(16)

        self._phase_label = QLabel("Phase: -")
        self._phase_label.setFont(QFont("", 9))
        status_layout.addWidget(self._phase_label)

        self._steps_label = QLabel("Steps: 0/0")
        self._steps_label.setFont(QFont("", 9))
        status_layout.addWidget(self._steps_label)

        self._errors_label = QLabel("Errors: 0")
        self._errors_label.setFont(QFont("", 9))
        status_layout.addWidget(self._errors_label)

        status_layout.addStretch()
        layout.addLayout(status_layout)

        # 分隔线
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)

        # 步骤列表滚动区域
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._steps_container = QWidget()
        self._steps_layout = QVBoxLayout(self._steps_container)
        self._steps_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._steps_layout.setSpacing(4)
        self._steps_layout.setContentsMargins(0, 0, 0, 0)

        self._scroll_area.setWidget(self._steps_container)
        layout.addWidget(self._scroll_area, 1)

        # 步骤组件映射
        self._step_widgets: dict[str, StepWidget] = {}

    def update_task_state(self, task_state: TaskState):
        """更新任务状态"""
        self._task_state = task_state

        # 更新标题
        self._title_label.setText(f"Task: {task_state.goal[:50]}...")

        # 更新进度
        progress = task_state.progress()
        self._progress_bar.setValue(int(progress["progress_percent"]))
        self._progress_bar.setFormat(f"{progress['completed']}/{progress['total']} steps")

        # 更新状态信息
        self._phase_label.setText(f"Phase: {task_state.phase.value.title()}")
        self._steps_label.setText(f"Steps: {progress['completed']}/{progress['total']}")
        self._errors_label.setText(f"Errors: {len(task_state.errors)}")

        # 更新步骤组件
        self._update_step_widgets(task_state.steps)

    def _update_step_widgets(self, steps: list[Step]):
        """更新步骤组件"""
        # 移除不再存在的步骤
        existing_ids = set(self._step_widgets.keys())
        current_ids = {s.id for s in steps}

        for step_id in existing_ids - current_ids:
            widget = self._step_widgets.pop(step_id)
            self._steps_layout.removeWidget(widget)
            widget.deleteLater()

        # 添加或更新步骤
        for i, step in enumerate(steps):
            if step.id in self._step_widgets:
                # 更新现有组件
                self._step_widgets[step.id].update_step(step)
            else:
                # 创建新组件
                widget = StepWidget(step)
                widget.clicked.connect(self._on_step_clicked)
                self._step_widgets[step.id] = widget
                self._steps_layout.insertWidget(i, widget)

    def _on_step_clicked(self, step_id: str):
        """步骤点击事件"""
        # 可以在这里显示步骤详情
        pass

    def add_step_event(self, step_id: str, event_type: str, data: dict = None):
        """添加步骤事件（实时更新）"""
        if step_id in self._step_widgets:
            # 这里可以添加实时事件显示逻辑
            pass


class TaskStepsPanel(QFrame):
    """任务步骤面板"""

    step_selected = pyqtSignal(str)  # step_id
    resume_clicked = pyqtSignal()    # 用户点击恢复按钮

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Raised)
        self._setup_ui()
        self._loaded_history = False
        self._resumable_state = None  # 可恢复的任务状态

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # 标题栏
        header_layout = QHBoxLayout()
        header_layout.setSpacing(8)

        title_label = QLabel("Task Steps")
        title_label.setFont(QFont("", 11, QFont.Weight.Bold))
        header_layout.addWidget(title_label)

        header_layout.addStretch()

        # 刷新按钮
        refresh_btn = QToolButton()
        refresh_btn.setText("↻")
        refresh_btn.setToolTip("Refresh task state")
        refresh_btn.clicked.connect(self._on_refresh)
        header_layout.addWidget(refresh_btn)

        layout.addLayout(header_layout)

        # 任务进度组件
        self._progress_widget = TaskProgressWidget()
        layout.addWidget(self._progress_widget, 1)

        # 历史任务栏
        self._history_bar = QFrame()
        self._history_bar.setFrameShape(QFrame.Shape.StyledPanel)
        self._history_bar.setCursor(Qt.CursorShape.PointingHandCursor)
        self._history_bar.setStyleSheet("""
            QFrame {
                background-color: #F3F4F6;
                border: 1px solid #E5E7EB;
                border-radius: 4px;
                padding: 6px;
            }
            QFrame:hover {
                background-color: #E5E7EB;
            }
        """)
        history_layout = QHBoxLayout(self._history_bar)
        history_layout.setContentsMargins(8, 6, 8, 6)
        history_layout.setSpacing(8)

        self._history_icon = QLabel("⏱")
        history_layout.addWidget(self._history_icon)

        self._history_label = QLabel("No recent tasks")
        self._history_label.setFont(QFont("", 9))
        self._history_label.setWordWrap(True)
        history_layout.addWidget(self._history_label, 1)

        self._history_bar.mousePressEvent = self._on_history_clicked
        layout.addWidget(self._history_bar)

        # 恢复按钮（默认隐藏，任务失败后显示）
        self._resume_btn = QToolButton()
        self._resume_btn.setText("▶ Resume from failed step")
        self._resume_btn.setToolTip("Reset failed steps and continue execution")
        self._resume_btn.setStyleSheet("""
            QToolButton {
                background-color: #3B82F6;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 12px;
                font-weight: bold;
            }
            QToolButton:hover {
                background-color: #2563EB;
            }
        """)
        self._resume_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._resume_btn.clicked.connect(self.resume_clicked.emit)
        self._resume_btn.hide()
        layout.addWidget(self._resume_btn)

        # 底部状态栏
        self._status_label = QLabel("No active task")
        self._status_label.setFont(QFont("", 8))
        self._status_label.setStyleSheet("color: #6B7280;")
        layout.addWidget(self._status_label)

        # 启动时扫描历史日志
        QTimer.singleShot(500, self._scan_history)

    def _scan_history(self):
        """扫描最近的历史任务日志"""
        try:
            from task_logger import get_latest_task_log, load_task_state_from_log
            latest = get_latest_task_log()
            if latest:
                state = load_task_state_from_log(latest)
                if state:
                    total = len(state.steps)
                    completed = len(state.get_completed_steps())
                    failed = len(state.get_failed_steps())
                    goal_short = state.goal[:40] + ("..." if len(state.goal) > 40 else "")
                    self._history_label.setText(
                        f"Last: {goal_short}\n{total} steps, {completed} ok, {failed} failed"
                    )
                    self._history_bar._log_path = str(latest)
                    self._history_bar._task_state = state
                    self._history_bar.show()
                    self._loaded_history = True
                else:
                    self._history_bar.hide()
            else:
                self._history_bar.hide()
        except Exception:
            self._history_bar.hide()

    def _on_history_clicked(self, event=None):
        """点击历史任务栏，加载并显示历史任务"""
        state = getattr(self._history_bar, '_task_state', None)
        if state:
            self._progress_widget.update_task_state(state)
            self._status_label.setText(
                f"History: task {state.task_id} - {state.phase.value.title()}"
            )

    def update_task_state(self, task_state: TaskState):
        """更新任务状态（实时任务）"""
        self._progress_widget.update_task_state(task_state)
        self._status_label.setText(f"Task {task_state.task_id}: {task_state.phase.value.title()}")
        # 有实时任务时隐藏历史栏和恢复按钮
        self._history_bar.hide()
        self._resume_btn.hide()

    def show_resume(self, task_state: TaskState):
        """任务失败后显示恢复按钮"""
        self._resumable_state = task_state
        self._resume_btn.setText("▶ Resume task")
        self._resume_btn.show()

    def get_resumable_state(self) -> TaskState | None:
        """获取可恢复的任务状态"""
        return self._resumable_state

    def add_step_event(self, step_id: str, event_type: str, data: dict = None):
        """添加步骤事件"""
        self._progress_widget.add_step_event(step_id, event_type, data)

    def _on_refresh(self):
        """刷新 — 恢复显示历史栏"""
        self._resume_btn.hide()
        self._resumable_state = None
        if self._loaded_history:
            self._history_bar.show()
            self._status_label.setText("No active task")
            self._progress_widget._step_widgets.clear()
            while self._progress_widget._steps_layout.count():
                child = self._progress_widget._steps_layout.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()
            self._progress_widget._title_label.setText("Task Progress")
            self._progress_widget._progress_bar.setValue(0)
            self._progress_widget._progress_bar.setFormat("0/0 steps")
            self._progress_widget._phase_label.setText("Phase: -")
            self._progress_widget._steps_label.setText("Steps: 0/0")
            self._progress_widget._errors_label.setText("Errors: 0")

    def clear(self):
        """清空任务状态"""
        self._progress_widget._step_widgets.clear()
        # 清空布局
        while self._progress_widget._steps_layout.count():
            child = self._progress_widget._steps_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self._status_label.setText("No active task")
        # 恢复历史栏
        QTimer.singleShot(100, self._scan_history)
