"""PyQt6 dialogs for Skill save prompt and Skill management."""

from __future__ import annotations

from typing import Any, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTextEdit,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QCheckBox,
    QWidget,
    QFormLayout,
)

from config import load_config
from skill_manager import Skill, SkillManager


def _is_light_theme() -> bool:
    app = QApplication.instance()
    if app is None:
        return False
    return str(app.property("theme_mode") or "dark").lower() == "light"


def _dialog_style() -> str:
    light = _is_light_theme()
    bg = "#ffffff" if light else "#1b1f24"
    panel = "#f8fafc" if light else "#232a31"
    border = "#d1d5db" if light else "#3b4552"
    text = "#111827" if light else "#e5e7eb"
    dim = "#4b5563" if light else "#9ca3af"
    input_bg = "#ffffff" if light else "#12161b"
    btn_bg = "#f3f4f6" if light else "#2b3440"
    btn_hover = "#e5e7eb" if light else "#3a4656"
    cfg = load_config()
    accent = str(getattr(cfg, "accent_color", "#5ee9ff") or "#5ee9ff").strip()
    if not accent.startswith("#"):
        accent = "#5ee9ff"
    primary = accent if light else accent
    primary_hover = "#1d4ed8" if light else "#2563eb"
    check_outline = accent
    return (
        f"QDialog{{background:{bg};color:{text};}}"
        f"QWidget{{color:{text};}}"
        f"QLabel{{color:{text};font-size:13px;}}"
        f"QLineEdit,QTextEdit,QListWidget{{"
        f"background:{input_bg};color:{text};border:1px solid {border};"
        "border-radius:8px;padding:6px;selection-background-color:#3b82f6;selection-color:#ffffff;"
        "}"
        f"QListWidget{{background:{panel};}}"
        f"QPushButton{{background:{btn_bg};color:{text};border:1px solid {border};"
        "border-radius:8px;padding:6px 12px;font-size:13px;font-weight:600;}}"
        f"QPushButton:hover{{background:{btn_hover};}}"
        f"QPushButton:pressed{{background:{btn_hover};}}"
        f"QPushButton:default{{background:{primary};color:#ffffff;border:1px solid {primary};}}"
        f"QPushButton:default:hover{{background:{primary_hover};border:1px solid {primary_hover};}}"
        f"QCheckBox{{color:{dim};font-size:13px;}}"
        f"QCheckBox::indicator{{width:16px;height:16px;border-radius:4px;border:2px solid {check_outline};background:transparent;}}"
        f"QCheckBox::indicator:unchecked{{background:transparent;}}"
        f"QCheckBox::indicator:checked{{background:{accent};border:2px solid {accent};}}"
        f"QCheckBox::indicator:hover{{border:2px solid {accent};}}"
        f"QScrollBar:vertical{{background:transparent;width:10px;margin:2px 0 2px 0;border:none;}}"
        f"QScrollBar::handle:vertical{{background:{border};min-height:32px;border-radius:3px;}}"
        f"QScrollBar::handle:vertical:hover{{background:{dim};}}"
        "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0px;}"
        f"QScrollBar:horizontal{{background:transparent;height:10px;margin:0 2px 0 2px;border:none;}}"
        f"QScrollBar::handle:horizontal{{background:{border};min-width:32px;border-radius:3px;}}"
        f"QScrollBar::handle:horizontal:hover{{background:{dim};}}"
        "QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{width:0px;}"
    )


class SkillSaveDialog(QDialog):
    """Semi-automatic crystallize: edit draft then save or skip."""

    def __init__(self, draft: dict[str, Any], parent=None):
        super().__init__(parent)
        self.setWindowTitle("保存为 Skill")
        self.resize(520, 420)
        self._saved = False
        self.setStyleSheet(_dialog_style())
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("将本次任务沉淀为可复用 Skill（仅保存在本机）。"))
        form = QFormLayout()
        self.name_ed = QLineEdit(str(draft.get("name", "")))
        kws = draft.get("intent_pattern") or []
        if isinstance(kws, list):
            kw_s = ", ".join(kws)
        else:
            kw_s = str(kws)
        self.kw_ed = QLineEdit(kw_s)
        self.steps_ed = QTextEdit()
        steps = draft.get("steps") or []
        if isinstance(steps, list):
            self.steps_ed.setPlainText("\n".join(str(s) for s in steps))
        else:
            self.steps_ed.setPlainText(str(steps))
        self.steps_ed.setMinimumHeight(160)
        form.addRow("名称", self.name_ed)
        form.addRow("关键词（逗号分隔）", self.kw_ed)
        form.addRow("步骤（每行一条）", self.steps_ed)
        lay.addLayout(form)
        row = QHBoxLayout()
        row.addStretch(1)
        skip = QPushButton("跳过")
        skip.clicked.connect(self.reject)
        save = QPushButton("保存")
        save.setDefault(True)
        save.clicked.connect(self._on_save)
        row.addWidget(skip)
        row.addWidget(save)
        lay.addLayout(row)

    def _on_save(self):
        name = self.name_ed.text().strip()
        if not name:
            QMessageBox.warning(self, "保存 Skill", "名称不能为空。")
            return
        steps = [ln.strip() for ln in self.steps_ed.toPlainText().splitlines() if ln.strip()]
        if not steps:
            QMessageBox.warning(self, "保存 Skill", "请至少填写一条步骤。")
            return
        kws = [x.strip().lower() for x in self.kw_ed.text().split(",") if x.strip()]
        SkillManager().upsert_skill(name=name, intent_pattern=kws, steps=steps)
        self._saved = True
        self.accept()

    def did_save(self) -> bool:
        return self._saved


class SkillsManagerDialog(QDialog):
    """List / edit / reorder / delete skills."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("管理 Skills")
        self.resize(700, 520)
        self._mgr = SkillManager()
        self.setStyleSheet(_dialog_style())
        root = QHBoxLayout(self)
        self.list_w = QListWidget()
        self.list_w.setMinimumWidth(200)
        self.list_w.currentRowChanged.connect(self._on_sel)
        root.addWidget(self.list_w)
        panel = QWidget()
        pl = QVBoxLayout(panel)
        form = QFormLayout()
        self.ed_name = QLineEdit()
        self.ed_kw = QLineEdit()
        self.ed_steps = QTextEdit()
        self.ed_steps.setMinimumHeight(180)
        self.chk_en = QCheckBox("启用")
        self.chk_en.setChecked(True)
        self.chk_en.toggled.connect(self._save_enabled_only)
        form.addRow("名称", self.ed_name)
        form.addRow("关键词（逗号）", self.ed_kw)
        form.addRow("步骤", self.ed_steps)
        form.addRow("", self.chk_en)
        pl.addLayout(form)
        btn_row = QHBoxLayout()
        self.btn_new = QPushButton("新建")
        self.btn_del = QPushButton("删除")
        self.btn_up = QPushButton("上移")
        self.btn_dn = QPushButton("下移")
        self.btn_save = QPushButton("保存当前")
        self.btn_new.clicked.connect(self._new_skill)
        self.btn_del.clicked.connect(self._del_skill)
        self.btn_up.clicked.connect(lambda: self._move(-1))
        self.btn_dn.clicked.connect(lambda: self._move(1))
        self.btn_save.clicked.connect(self._save_current)
        for b in (self.btn_new, self.btn_del, self.btn_up, self.btn_dn, self.btn_save):
            btn_row.addWidget(b)
        pl.addLayout(btn_row)
        pl.addStretch(1)
        root.addWidget(panel, 1)
        self._reload_list(select_id=None)

    def _all_ids_in_order(self) -> list[str]:
        return [s.id for s in self._mgr.list_skills()]

    def _reload_list(self, select_id: Optional[str] = None):
        self.list_w.blockSignals(True)
        self.list_w.clear()
        skills = self._mgr.list_skills()
        sel_row = 0
        for i, sk in enumerate(skills):
            flag = "" if sk.enabled else " [已禁用]"
            it = QListWidgetItem(f"{sk.name}{flag}")
            it.setData(Qt.ItemDataRole.UserRole, sk.id)
            self.list_w.addItem(it)
            if select_id and sk.id == select_id:
                sel_row = i
        self.list_w.setCurrentRow(sel_row)
        self.list_w.blockSignals(False)
        self._on_sel(sel_row)

    def _current_skill_id(self) -> Optional[str]:
        it = self.list_w.currentItem()
        if not it:
            return None
        return str(it.data(Qt.ItemDataRole.UserRole) or "")

    def _on_sel(self, row: int):
        sid = self._current_skill_id()
        if not sid:
            self.ed_name.clear()
            self.ed_kw.clear()
            self.ed_steps.clear()
            return
        sk = self._mgr.load_skill(sid)
        if not sk:
            return
        self.ed_name.setText(sk.name)
        self.ed_kw.setText(", ".join(sk.intent_pattern))
        self.ed_steps.setPlainText("\n".join(sk.steps))
        self.chk_en.setChecked(sk.enabled)

    def _new_skill(self):
        draft = Skill(
            id="",
            name="新 Skill",
            intent_pattern=["keyword"],
            steps=["第一步", "第二步"],
            enabled=True,
        )
        sk = self._mgr.upsert_skill(name=draft.name, intent_pattern=draft.intent_pattern, steps=draft.steps)
        self._reload_list(select_id=sk.id)

    def _del_skill(self):
        sid = self._current_skill_id()
        if not sid:
            return
        if QMessageBox.question(self, "删除", f"确定删除 Skill「{sid}」？") != QMessageBox.StandardButton.Yes:
            return
        self._mgr.delete_skill(sid)
        self._reload_list()

    def _move(self, delta: int):
        ids = self._all_ids_in_order()
        sid = self._current_skill_id()
        if not sid or sid not in ids:
            return
        i = ids.index(sid)
        j = i + delta
        if j < 0 or j >= len(ids):
            return
        ids[i], ids[j] = ids[j], ids[i]
        self._mgr.reorder(ids)
        self._reload_list(select_id=sid)

    def _save_current(self):
        sid = self._current_skill_id()
        if not sid:
            return
        sk = self._mgr.load_skill(sid)
        if not sk:
            return
        name = self.ed_name.text().strip() or sk.name
        kws = [x.strip().lower() for x in self.ed_kw.text().split(",") if x.strip()]
        steps = [ln.strip() for ln in self.ed_steps.toPlainText().splitlines() if ln.strip()]
        if not steps:
            QMessageBox.warning(self, "Skills", "步骤不能为空。")
            return
        updated = Skill(
            id=sid,
            name=name,
            intent_pattern=kws,
            steps=steps,
            success_count=sk.success_count,
            last_used_at=sk.last_used_at,
            enabled=self.chk_en.isChecked(),
        )
        self._mgr.update_skill_full(updated)
        QMessageBox.information(self, "Skills", "已保存。")
        self._reload_list(select_id=sid)

    def _save_enabled_only(self, checked: bool):
        sid = self._current_skill_id()
        if not sid:
            return
        sk = self._mgr.load_skill(sid)
        if not sk:
            return
        if sk.enabled == bool(checked):
            return
        sk.enabled = bool(checked)
        self._mgr.update_skill_full(sk)
        self._reload_list(select_id=sid)
