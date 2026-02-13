"""
Plan Widget for Monitor Screen
Visualizes the central_plan.md status and tasks.
"""

import os
import re
import json
import time
import traceback
from io import StringIO
from typing import Dict, List, Optional
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static, Label
from textual.containers import Vertical, VerticalScroll, Container
from textual import work
from textual.worker import Worker
from backend.utils.logger import Logger



class PlanTaskItem(Vertical):
    """Display a single task in the plan with full details"""
    DEFAULT_CSS = """
    PlanTaskItem {
        background: $surface;
        border: solid $secondary;
        padding: 1;
        margin-bottom: 1;
        height: auto;
        min-height: 5;
    }
    
    PlanTaskItem.status-done {
        border-left: solid $success;
    }
    
    PlanTaskItem.status-in-progress {
        border-left: solid $primary;
    }
    
    PlanTaskItem.status-pending {
        border-left: solid $border;
    }
    
    PlanTaskItem .header {
        text-style: bold;
        padding-bottom: 1;
        height: auto;
    }
    
    PlanTaskItem .description {
        color: $text;
        padding-bottom: 1;
        height: auto;
        width: 100%;
    }
    
    PlanTaskItem .meta {
        color: $text-muted;
        text-style: italic;
        height: auto;
    }
    
    PlanTaskItem .result {
        background: $surface-darken-1;
        color: $success;
        border-top: solid $success;
        padding: 1;
        margin-top: 1;
        height: auto;
    }
    """
    
    def __init__(self, task: Dict):
        super().__init__()
        self.task_data = task
        self.task_id = task.get("id", "?")
        self.status = task.get("status", "PENDING")
        
        # Set class based on status
        self._apply_status_class()

    def _apply_status_class(self):
        self.remove_class("status-done", "status-in-progress", "status-pending")
        if self.status == "DONE":
            self.add_class("status-done")
        elif self.status == "IN_PROGRESS":
            self.add_class("status-in-progress")
        else:
            self.add_class("status-pending")
            
    def compose(self) -> ComposeResult:
        icon_map = {
            "DONE": "✅",
            "IN_PROGRESS": "🔄",
            "PENDING": "⏳",
            "BLOCKED": "🚫"
        }
        icon = icon_map.get(self.status, "•")
        
        # Header: ID + Status + Type
        task_id = self.task_data.get('id', '?')
        task_type = self.task_data.get('type', 'standard')
        title = f"{icon} [bold]#{task_id}[/bold] {self.status} | {task_type}"
        yield Label(title, classes="header", id="task-header")
        
        # Body: Description
        desc = self.task_data.get('description', 'No description')
        yield Static(desc, classes="description", id="task-desc")
        
        # Meta: Assignees & Dependencies
        assignees = ", ".join(map(str, self.task_data.get("assignees", []))) or "Unassigned"
        deps = ", ".join(map(str, self.task_data.get("dependencies", []))) or "None"
        
        yield Label(f"👤 Assignees: {assignees}", classes="meta", id="task-assignees")
        yield Label(f"🔗 Depends on: {deps}", classes="meta", id="task-deps")
            
        # Result Summary (if available)
        result = self.task_data.get("result_summary", "")
        res_label = Label(f"📄 Result: {result}", classes="result", id="task-result")
        res_label.display = bool(result)
        yield res_label
    def update_data(self, task: Dict):
        """Update the card with new data in-place"""
        if self.task_data == task:
            return
            
        self.task_data = task
        old_status = self.status
        self.status = task.get("status", "PENDING")
        
        if old_status != self.status:
            self._apply_status_class()
            
        icon_map = {"DONE": "✅", "IN_PROGRESS": "🔄", "PENDING": "⏳", "BLOCKED": "🚫"}
        icon = icon_map.get(self.status, "•")
        task_id = task.get('id', '?')
        task_type = task.get('type', 'standard')
        
        self.query_one("#task-header", Label).update(f"{icon} [bold]#{task_id}[/bold] {self.status} | {task_type}")
        self.query_one("#task-desc", Static).update(task.get('description', 'No description'))
        
        assignees = ", ".join(map(str, task.get("assignees", []))) or "Unassigned"
        deps = ", ".join(map(str, task.get("dependencies", []))) or "None"
        self.query_one("#task-assignees", Label).update(f"👤 Assignees: {assignees}")
        self.query_one("#task-deps", Label).update(f"🔗 Depends on: {deps}")
        
        result = task.get("result_summary", "")
        res_label = self.query_one("#task-result", Label)
        if result:
            res_label.update(f"📄 Result: {result}")
            res_label.display = True
        else:
            res_label.display = False


class PlanWidget(Widget):
    """
    Widget to visualize the Central Plan.
    Polls the central_plan.md file for changes.
    """
    
    DEFAULT_CSS = """
    PlanWidget {
        background: $surface-darken-1;
        border-left: solid $primary;
        height: 100%;
        width: 100%;
    }
    
    PlanWidget .header {
        dock: top;
        background: $primary;
        color: $background;
        padding: 0 1;
        text-style: bold;
    }
    
    PlanWidget #mission-info-container {
        padding: 1;
        background: $surface;
        border-bottom: solid $secondary;
        height: auto;
    }
    
    PlanWidget .stats-container {
        height: auto;
        margin-top: 1;
        padding: 0 1;
        border-top: solid $secondary;
        background: $surface-darken-1;
    }
    
    PlanWidget .stat-item {
        width: 100%;
        color: $text-muted;
        padding: 0 1;
    }
    
    PlanWidget #task-list {
        padding: 1;
        height: auto;
    }
    
    PlanWidget .summary {
        padding: 1 0;
        color: $text;
        height: auto;
    }
    """
    
    def __init__(self, blackboard_dir: str, id: Optional[str] = None):
        super().__init__(id=id)
        self.blackboard_dir = blackboard_dir
        self.plan_data = None
        self.last_mtime = 0
        self.is_monitoring = False
        self.poll_worker = None
        
    def compose(self) -> ComposeResult:
        yield Label("📋 Mission Plan", classes="header")
        
        with VerticalScroll(id="content-scroll"):
            with Container(id="mission-info-container"):
                yield Label("", id="plan-goal")
                yield Label("", id="plan-status")
                yield Static("", id="plan-summary", classes="summary")
                with Container(classes="stats-container", id="stats-bar"):
                    yield Label("", id="stat-total", classes="stat-item")
                    yield Label("", id="stat-done", classes="stat-item")
                    yield Label("", id="stat-progress", classes="stat-item")
                    yield Label("", id="stat-pending", classes="stat-item")
                    yield Label("", id="stat-blocked", classes="stat-item")
            
            yield Vertical(id="task-list")
            
    def on_mount(self):
        self.is_monitoring = True
        self.start_polling()
        
    def on_unmount(self):
        self.is_monitoring = False
        if self.poll_worker:
            self.poll_worker.cancel()
        
    def start_polling(self):
        self.poll_worker = self.poll_plan_file()
        
    @work(exclusive=True, thread=True)
    def poll_plan_file(self):
        """Poll central_plan.md for changes"""
        plan_path = os.path.join(self.blackboard_dir, "global_indices", "central_plan.md")
        
        while self.is_monitoring:
            try:
                if os.path.exists(plan_path):
                    mtime = os.path.getmtime(plan_path)
                    if mtime > self.last_mtime:
                        self.last_mtime = mtime
                        content = self._read_plan(plan_path)
                        if content:
                            self.app.call_from_thread(self.update_plan, content)
                        else:
                            # self.app.call_from_thread(self.notify, f"Failed to parse plan", severity="warning")
                            pass
                else:
                    # self.app.call_from_thread(self.notify, f"Plan file not found: {plan_path}", severity="warning")
                    pass
            except Exception as e:
                self._log_exception("Poll Error", e)
                # self.app.call_from_thread(self.notify, f"Poll Error: {e}", severity="error")
                pass
            time.sleep(2)

    def _read_plan(self, path: str) -> Optional[Dict]:
        """Read and parse the JSON block from markdown"""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                text = f.read()
            
            # Extract JSON block: Look for ```json ... ```
            # We use a non-greedy dot match
            # Match greedily to catch nested structures
            match = re.search(r"```json\s*(\{.*\}).*?```", text, re.DOTALL)
            if match:
                return json.loads(match.group(1))
            
            return None
        except Exception as e:
            # self._log_exception(f"Read/Parse Error: {path}", e)
            return None

    def _log_exception(self, context: str, error: Exception) -> None:
        """Log detailed exception info for debugging."""
        error_type = type(error).__name__
        Logger.error(f"[PlanWidget] {context}: {error_type}: {error}")
        # StylesheetErrors in Textual often carries a list in .errors
        errors_obj = getattr(error, "errors", None)
        if errors_obj:
            try:
                from rich.console import Console
                buffer = StringIO()
                Console(file=buffer, force_terminal=False, width=200).print(errors_obj)
                rendered = buffer.getvalue().rstrip()
                if rendered:
                    Logger.error(f"[PlanWidget] Stylesheet errors: {rendered}")
                else:
                    Logger.error(f"[PlanWidget] Stylesheet errors: {errors_obj}")
            except Exception:
                Logger.error(f"[PlanWidget] Stylesheet errors: {errors_obj}")
        tb_str = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        Logger.debug(f"[PlanWidget] Traceback:\n{tb_str}")

    def update_plan(self, data: Dict):
        """Update UI with new plan data - Optimized in-place updates"""
        if self.plan_data == data:
            return
            
        self.plan_data = data
        
        goal = data.get("mission_goal", "No Goal Set")
        status = data.get("status", "UNKNOWN")
        summary = data.get("summary", "No summary available.")
        tasks = data.get("tasks", [])
        
        # Update Info Header
        self.query_one("#plan-goal", Label).update(f"🎯 Goal: {goal}")
        self.query_one("#plan-status", Label).update(f"📊 Status: {status}")
        self.query_one("#plan-summary", Static).update(f"Summary: {summary}")
            
        # Update Stats Bar
        total = len(tasks)
        pending = sum(1 for t in tasks if t.get("status") == "PENDING")
        in_progress = sum(1 for t in tasks if t.get("status") == "IN_PROGRESS")
        done = sum(1 for t in tasks if t.get("status") == "DONE")
        blocked = sum(1 for t in tasks if t.get("status") == "BLOCKED")

        self.query_one("#stat-total", Label).update(f"Total: {total}")
        self.query_one("#stat-done", Label).update(f"✅ done: {done}")
        self.query_one("#stat-progress", Label).update(f"🔄 in progress: {in_progress}")
        self.query_one("#stat-pending", Label).update(f"⏳ pending: {pending}")
        self.query_one("#stat-blocked", Label).update(f"🚫 blocked: {blocked}")
        
        # Update Task List In-place
        task_list = self.query_one("#task-list", Vertical)
        
        # Build map of existing task widgets
        existing_widgets = {child.task_id: child for child in task_list.children if isinstance(child, PlanTaskItem)}
        
        # Order matters, so if structure changes, we might still need to rebuild
        # But for now let's try updating existing and mounting new
        new_task_ids = [t.get("id") for t in tasks]
        existing_ids = list(existing_widgets.keys())
        
        if new_task_ids != existing_ids:
            # If order changed or items removed, full rebuild is safest for layout
            task_list.remove_children()
            for task in tasks:
                task_list.mount(PlanTaskItem(task))
        else:
            # Perfect match, update in-place
            for task in tasks:
                tid = task.get("id")
                existing_widgets[tid].update_data(task)
