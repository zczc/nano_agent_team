"""
Agent Monitor Screen
Visualizes Swarm Agent execution with Role at the top and Linear Trajectory in the middle.
"""

import os
import json
import time
import fcntl
from typing import Dict, Optional, List
from backend.utils.logger import Logger
from backend.infra.config import Config
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static, Input, ListView, ListItem, Label, Markdown, ContentSwitcher
from textual.containers import Vertical, Horizontal, VerticalScroll, Container
from textual.binding import Binding
from textual import on, work
from textual.worker import Worker
from textual.reactive import reactive

from ..components.plan_widget import PlanWidget

# --- Constants ---
MAX_CACHED_ENTRIES = 20  # Max log entries cached per agent in memory
MAX_TRAJECTORY_WIDGETS = 20  # Max trajectory widgets visible in UI
LOG_POLL_INTERVAL = 1.0  # Seconds between polling all log files

from backend.utils.file_utils import sanitize_filename

# --- Widgets ---

class TrajectoryItem(Static):
    """A single item in the agent's execution trajectory."""
    def __init__(self, item_type: str, content: str, timestamp: float):
        super().__init__()
        self.item_type = item_type
        self.content = content
        self.timestamp = timestamp
        self.time_str = time.strftime("%H:%M:%S", time.localtime(timestamp))
        self.add_class(f"type-{item_type}")

    def compose(self) -> ComposeResult:
        icon = self._get_icon()
        with Horizontal(classes="trajectory-item-header"):
            yield Label(f"{icon} {self.item_type.upper()}", classes="item-type")
            yield Label(f"{self.time_str}", classes="item-time")
        
        if self.item_type == "tool_call":
            yield Static(self.content, classes="item-content-text", markup=False)
        else:
            yield Static(self.content, classes="item-content-text", markup=False)

    def _get_icon(self) -> str:
        icons = {
            "thought": "💭",
            "tool_call": "🛠️",
            "tool_result": "📄",
            "intervention": "👤",
            "error": "❌",
            "lifecycle": "🔄"
        }
        return icons.get(self.item_type, "•")

    DEFAULT_CSS = """
    TrajectoryItem {
        margin: 0 1 1 1;
        padding: 0 1;
        background: $surface;
        border-left: solid $primary;
        height: auto;
    }
    
    TrajectoryItem .trajectory-item-header {
        height: 1;
        margin-bottom: 0;
    }
    
    TrajectoryItem .item-type {
        text-style: bold;
        color: $accent;
        width: 1fr;
    }
    
    TrajectoryItem .item-time {
        color: $text-muted;
        text-style: italic;
    }
    
    TrajectoryItem .item-content-text {
        padding: 0 1;
        color: $text;
    }
    
    TrajectoryItem .item-content-markdown {
        padding: 0 1;
    }

    TrajectoryItem.type-thought { border-left: solid $primary; }
    TrajectoryItem.type-tool_call { border-left: solid $warning; background: $surface-darken-1; }
    TrajectoryItem.type-tool_result { border-left: solid $success; background: $surface-darken-2; }
    TrajectoryItem.type-intervention { border-left: solid $accent; background: $accent-darken-3; }
    TrajectoryItem.type-error { border-left: solid $error; background: $error-darken-3; }
    """


class AgentListItem(ListItem):
    """List item for an agent"""
    agent_status = reactive("UNKNOWN")
    agent_pid = reactive(0)
    is_selected = reactive(False)

    def __init__(self, name: str, status: str, pid: int):
        super().__init__()
        self.agent_name = name
        self.agent_status = status
        self.agent_pid = pid
        
    def compose(self) -> ComposeResult:
        prefix = "▶ " if self.is_selected else "  "
        yield Label(f"{prefix}{self._get_icon()} {self.agent_name}", classes="agent-name", id="name-label")
        yield Label(f"PID: {self.agent_pid}", classes="agent-pid", id="pid-label")

    def _get_icon(self) -> str:
        status_map = {
            "RUNNING": "🟢",
            "DEAD": "🔴",
            "IDLE": "⚪",
            "UNKNOWN": "❓"
        }
        return status_map.get(self.agent_status, "❓")

    def watch_agent_status(self, value: str):
        self._update_name_label()

    def watch_is_selected(self, value: bool):
        self._update_name_label()

    def _update_name_label(self):
        try:
            lbl = self.query_one("#name-label", Label)
            prefix = "▶ " if self.is_selected else "  "
            lbl.update(f"{prefix}{self._get_icon()} {self.agent_name}")
        except Exception:
            pass  # Widget may not be mounted yet during reactive init

    def watch_agent_pid(self, value: int):
        try:
            lbl = self.query_one("#pid-label", Label)
            lbl.update(f"PID: {value}")
        except Exception:
            pass  # Widget may not be mounted yet during reactive init


class AgentMonitorScreen(Screen):
    """
    Screen for monitoring Swarm Agents.
    Layout: Left (Agent List), Middle (Role Top, Trajectory Mid, Input Bottom), Right (Plan)
    """
    
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("ctrl+c", "app.quit", "Quit"),
    ]
    
    DEFAULT_CSS = """
    AgentMonitorScreen {
        background: $background;
    }
    
    AgentMonitorScreen #screen-header {
        dock: top;
        height: 1;
        background: $primary;
        color: $background;
        padding: 0 1;
        text-style: bold;
    }
    
    #main-container {
        height: 1fr;
        layout: horizontal;
    }
    
    #agent-list-container {
        width: 20%;
        height: 100%;
        border-right: solid $primary;
        background: $surface-darken-1;
    }
    
    #middle-container {
        width: 50%;
        height: 100%;
        layout: vertical;
        border-right: solid $primary;
    }

    #role-box {
        height: auto;
        max-height: 15;
        background: $surface;
        border-bottom: solid $primary;
        scrollbar-gutter: stable;
        overflow-y: auto;
    }

    #role-header {
        background: $secondary;
        color: $text;
        text-style: bold;
        padding: 0 1;
        width: 100%;
    }

    #role-display {
        padding: 0 1;
        color: $text-muted;
    }
    
    #trajectory-scroll {
        height: 1fr;
    }
    
    .trajectory-scroll {
        height: 1fr;
        background: $background;
        scrollbar-gutter: stable;
        padding: 1 0;
    }

    .omitted-hint {
        color: $text-muted;
        text-align: center;
        width: 100%;
        padding: 1;
        text-style: italic;
    }
    
    #input-container {
        height: auto;
        padding: 1;
        background: $surface-darken-1;
        border-top: solid $primary;
    }
    
    #plan-container {
        width: 30%;
        height: 100%;
        background: $surface-darken-1;
    }
    
    PlanWidget {
        width: 100%;
        height: 100%;
    }
    
    AgentListItem {
        height: auto;
        padding: 1 2;
        border-bottom: solid $secondary;
        layout: vertical;
        border-left: wide transparent;
    }
    
    AgentListItem:hover {
        background: $primary 20%;
        color: $text;
        text-style: italic;
    }
    
    AgentListItem.-selected {
        background: $primary;
        color: $background;
        text-style: bold;
        border-left: wide $primary;
        padding-left: 1;
    }
    
    AgentListItem.-selected .agent-name {
        text-style: bold;
        color: $background;
    }
    
    AgentListItem.-selected .agent-pid {
        color: $background;
        text-style: bold italic;
    }
    
    .agent-pid {
        color: $text-muted;
        text-style: italic;
    }

    .section-title {
        background: $primary-darken-1;
        color: $text;
        text-style: bold;
        padding: 0 1;
    }
    """

    def __init__(self, blackboard_dir: Optional[str] = None):
        super().__init__()
        # Always use Config.BLACKBOARD_ROOT as the source of truth (not cached)
        from backend.infra.config import Config
        Logger.info(f"[Monitor] Using Config.BLACKBOARD_ROOT: {Config.BLACKBOARD_ROOT}")
        self.selected_agent: Optional[str] = None
        self.is_monitoring = False
        self.registry_worker: Optional[Worker] = None
        
        # State mapping: agent_name -> data
        self.agent_entries: Dict[str, List[Dict]] = {}  # Cache log entries (capped)
        self.agent_roles: Dict[str, str] = {}
        self.agent_list_cache: Dict[str, Dict] = {}
        # Track widget count per agent to avoid expensive DOM queries
        self.trajectory_widget_count: int = 0
        self._has_omitted_hint: bool = False

        # Unified log polling state (single thread)
        self._active_agent_logs: Dict[str, str] = {}  # agent_name -> log_path
        self._file_positions: Dict[str, int] = {}  # agent_name -> file position
        self._log_poll_worker: Optional[Worker] = None

    def compose(self) -> ComposeResult:
        yield Static("🔍 Swarm Agent Monitor", id="screen-header")
        
        with Container(id="main-container"):
            # Left: Agent List
            with Vertical(id="agent-list-container"):
                yield Label("Agents", classes="section-title")
                yield ListView(id="agent-list")
            
            # Middle: Role Top, Trajectory Mid, Input Bottom
            with Vertical(id="middle-container"):
                with Vertical(id="role-box"):
                    yield Label("📋 Agent Role", id="role-header")
                    yield Markdown("", id="role-display")
                
                # Single scroll for all agents
                yield VerticalScroll(id="trajectory-scroll", classes="trajectory-scroll")
                
                with Vertical(id="input-container"):
                    yield Input(placeholder="Select an agent to intervene...", id="intervention-input", disabled=True)
                    yield Static("Commands: Type message to intervene", classes="input-hint")

            # Right: Mission Plan
            yield PlanWidget(blackboard_dir=Config.BLACKBOARD_ROOT, id="plan-container")

    def on_mount(self):
        # Immediately load existing registry data before starting polling
        self.load_initial_registry()
        self.start_registry_polling()
        
    def on_unmount(self):
        self.is_monitoring = False
        # Cancel background workers to ensure clean teardown
        if self.registry_worker:
            self.registry_worker.cancel()
        if self._log_poll_worker:
            self._log_poll_worker.cancel()

    def load_initial_registry(self):
        """Load existing registry data immediately on mount"""
        registry_path = os.path.join(Config.BLACKBOARD_ROOT, "registry.json")
        try:
            if os.path.exists(registry_path):
                with open(registry_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.update_registry_data(data)
                Logger.info("[Monitor] Initial registry data loaded")
        except Exception as e:
            Logger.error(f"[Monitor] Failed to load initial registry: {e}")

    def start_registry_polling(self):
        self.is_monitoring = True
        self.registry_worker = self.poll_registry()
        self._log_poll_worker = self.poll_all_logs()

    @work(exclusive=True, thread=True)
    def poll_registry(self):
        """Poll registry.json to update agent list and start background workers"""
        registry_path = os.path.join(Config.BLACKBOARD_ROOT, "registry.json")
        last_mtime = 0
        
        while self.is_monitoring:
            try:
                if os.path.exists(registry_path):
                    # mtime = os.path.getmtime(registry_path)
                    # if mtime > last_mtime:
                    #     last_mtime = mtime
                    with open(registry_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    self.app.call_from_thread(self.update_registry_data, data)
            except Exception as e:
                Logger.debug(f"[Monitor] Registry poll error: {e}")
            time.sleep(2)

    @work(exclusive=True, thread=True)
    def poll_all_logs(self):
        """Single thread to poll all agent log files"""
        while self.is_monitoring:
            try:
                # Snapshot current agents to avoid modification during iteration
                current_agents = list(self._active_agent_logs.items())

                for agent_name, log_path in current_agents:
                    if not self.is_monitoring:
                        break
                    try:
                        if not os.path.exists(log_path):
                            continue

                        # Get last known position
                        last_pos = self._file_positions.get(agent_name, 0)

                        with open(log_path, 'r', encoding='utf-8') as f:
                            f.seek(last_pos)
                            new_lines = f.readlines()
                            new_pos = f.tell()

                        if new_lines:
                            # Update position
                            self._file_positions[agent_name] = new_pos
                            # Process in main thread
                            self.app.call_from_thread(self.process_log_batch, agent_name, new_lines)

                    except Exception as e:
                        Logger.debug(f"[Monitor] Log poll error for {agent_name}: {e}")

            except Exception as e:
                Logger.error(f"[Monitor] poll_all_logs error: {e}")

            time.sleep(LOG_POLL_INTERVAL)

    def update_registry_data(self, data: Dict):
        # 1. Update Agent List UI
        new_cache = {}
        for name, info in data.items():
            stripped_name = name.strip()
            new_cache[stripped_name] = {"status": info.get("status"), "pid": info.get("pid")}
            
        if new_cache != self.agent_list_cache:
            self.agent_list_cache = new_cache
            list_view = self.query_one("#agent-list", ListView)
            
            existing_agents = {}
            for child in list_view.children:
                if isinstance(child, AgentListItem):
                    existing_agents[child.agent_name.strip()] = child
            
            for name, info in new_cache.items():
                if name in existing_agents:
                    item = existing_agents[name]
                    item.agent_status = info["status"]
                    item.agent_pid = info["pid"]
                else:
                    list_view.append(AgentListItem(name, info["status"], info["pid"]))
            
            # Remove agents that are no longer in registry
            for name, item in existing_agents.items():
                if name not in new_cache:
                    item.remove()
            
            if not self.selected_agent and list_view.children:
                list_view.index = 0
                first_item = list_view.children[0]
                if isinstance(first_item, AgentListItem):
                    self._select_agent_by_name(first_item.agent_name)

        # 2. Register new agents to unified log polling
        for agent_name, info in new_cache.items():
            if agent_name not in self.agent_entries:
                # Init per-agent cache
                self.agent_entries[agent_name] = []
                # Register to unified polling
                safe_name = sanitize_filename(agent_name)
                log_path = os.path.join(Config.BLACKBOARD_ROOT, "logs", f"{safe_name}.jsonl")
                self._active_agent_logs[agent_name] = log_path
                self._file_positions[agent_name] = 0  # Start from beginning

        # 3. Remove agents that are no longer in registry from polling
        for agent_name in list(self._active_agent_logs.keys()):
            if agent_name not in new_cache:
                del self._active_agent_logs[agent_name]
                if agent_name in self._file_positions:
                    del self._file_positions[agent_name]

    @on(ListView.Selected, "#agent-list")
    def on_agent_selected(self, event: ListView.Selected):
        item = event.item
        if isinstance(item, AgentListItem):
            Logger.info(f"[Monitor] Agent Selected: {item.agent_name}")
            self._select_agent_by_name(item.agent_name)

    @on(ListView.Highlighted, "#agent-list")
    def on_agent_highlighted(self, event: ListView.Highlighted):
        item = event.item
        if isinstance(item, AgentListItem):
            # Also switch on highlight for better UX
            self._select_agent_by_name(item.agent_name)

    def _select_agent_by_name(self, agent_name: str):
        stripped_name = agent_name.strip()
        if self.selected_agent == stripped_name:
            return # Skip if already selected
            
        Logger.info(f"[Monitor] Switching to agent: {stripped_name}")
        self.selected_agent = stripped_name

        # Update Selection Indicators
        try:
            list_view = self.query_one("#agent-list", ListView)
            for child in list_view.children:
                if isinstance(child, AgentListItem):
                    child.is_selected = (child.agent_name.strip() == stripped_name)
        except Exception:
            pass
        
        # Update Input
        try:
            inp = self.query_one("#intervention-input", Input)
            inp.disabled = False
            inp.placeholder = f"Message to {stripped_name}..."
        except Exception:
            pass  # Input widget may not be ready
        
        # Clear and Fill Trajectory (Windowed: Last N)
        try:
            scroll = self.query_one("#trajectory-scroll", VerticalScroll)
            scroll.query("*").remove()
            self.trajectory_widget_count = 0
            self._has_omitted_hint = False
            
            all_entries = self.agent_entries.get(stripped_name, [])
            display_entries = all_entries[-MAX_TRAJECTORY_WIDGETS:]
            
            if len(all_entries) > MAX_TRAJECTORY_WIDGETS:
                scroll.mount(Static("... (Earlier history omitted)", classes="omitted-hint"))
                self._has_omitted_hint = True
            
            for entry in display_entries:
                try:
                    scroll.mount(TrajectoryItem(
                        entry["type"], 
                        entry["content"], 
                        entry["timestamp"]
                    ))
                    self.trajectory_widget_count += 1
                except Exception as e:
                    Logger.error(f"[Monitor] Failed to mount trajectory item: {e}")
            
            scroll.scroll_end(animate=False)
        except Exception as e:
            Logger.error(f"[Monitor] Failed to refresh trajectory for {stripped_name}: {e}")
        
        # Update Role from cache (falls back to empty if not found)
        try:
            role = self.agent_roles.get(stripped_name, "")
            self.query_one("#role-display", Markdown).update(role)
        except Exception:
            pass  # Markdown widget may not be ready

    def process_log_batch(self, agent_name: str, lines: List[str]):
        Logger.info(f"[Monitor] Processing batch for {agent_name}: {len(lines)} lines")
        for line in lines:
            self.process_log_line(agent_name, line)

    def process_log_line(self, agent_name: str, line: str):
        try:
            if not line.strip(): return
            entry = json.loads(line)
            evt_type = entry.get("type")
            data = entry.get("data")
            timestamp = entry.get("timestamp", time.time())
            
            agent_name = agent_name.strip()
            # 0. Cache for memory-based fast switching
            if agent_name not in self.agent_entries:
                self.agent_entries[agent_name] = []

            # 1. Role Extraction (Cache and Update Widget if active)
            if evt_type == "system_prompt":
                content = data.get("content", "")
                role_text = ""
                for marker in ["# YOUR ROLE", "# 角色"]:
                    if marker in content:
                        role_text = content.split(marker)[-1].strip()
                        stop_markers = [
                            "\n**行为协议**", "\n**Behavior Protocol**",
                            "\n**PROTOCOL**", "\n##", "\n#"
                        ]
                        for stop_marker in stop_markers:
                            if stop_marker in role_text:
                                role_text = role_text.split(stop_marker)[0].strip()
                        break
                
                if not role_text:
                    role_text = content[:500] + "..." if len(content) > 500 else content

                self.agent_roles[agent_name] = role_text
                if self.selected_agent == agent_name:
                    self.query_one("#role-display", Markdown).update(role_text)
                return

            # 2. Build Entry Data with Truncation (200 chars)
            new_items = []
            def get_truncated(text: str) -> str:
                if len(text) > 200:
                    return text[:200] + "..."
                return text

            if evt_type == "message":
                # Only extract thought content from message events.
                # tool_calls are handled by the dedicated 'tool_call' event to avoid duplication.
                msg_content = data.get("content", "")
                if msg_content:
                    new_items.append({"type": "thought", "content": get_truncated(msg_content), "timestamp": timestamp})

            elif evt_type == "tool_call":
                calls = data.get("tool_calls", [])
                for call in calls:
                    fn = call.get("function", {})
                    call_text = f"Call: {fn.get('name')}({fn.get('arguments')})"
                    new_items.append({"type": "tool_call", "content": get_truncated(call_text), "timestamp": timestamp})

            elif evt_type == "tool_result":
                result = data.get("result", "")
                new_items.append({"type": "tool_result", "content": get_truncated(result), "timestamp": timestamp})

            elif evt_type == "intervention":
                new_items.append({"type": "intervention", "content": get_truncated(data.get("content", "")), "timestamp": timestamp})

            elif evt_type == "lifecycle":
                evt = data.get("event", "")
                reason = data.get("reason", "")
                new_items.append({"type": "lifecycle", "content": f"{evt}: {reason}", "timestamp": timestamp})

            elif evt_type == "error":
                new_items.append({"type": "error", "content": get_truncated(data.get("error", "Unknown Error")), "timestamp": timestamp})

            # 3. Commit to Cache
            # Only update UI if this agent is currently selected
            is_active = (self.selected_agent == agent_name)

            for item_data in new_items:
                self.agent_entries[agent_name].append(item_data)

                # Cap in-memory cache per agent
                entries = self.agent_entries[agent_name]
                if len(entries) > MAX_CACHED_ENTRIES:
                    self.agent_entries[agent_name] = entries[-MAX_CACHED_ENTRIES:]

                # Skip UI update for non-active agents (only cache)
                if not is_active:
                    continue

                # Only update UI for selected agent
                try:
                    scroll = self.query_one("#trajectory-scroll", VerticalScroll)
                except Exception:
                    continue  # Widget not ready

                # Smart Scroll: Check if at bottom BEFORE mounting
                at_bottom = scroll.scroll_y >= scroll.max_scroll_y

                # Mount new trajectory widget
                try:
                    scroll.mount(TrajectoryItem(
                        item_data["type"],
                        item_data["content"],
                        item_data["timestamp"]
                    ))
                    self.trajectory_widget_count += 1
                except Exception as e:
                    Logger.error(f"[Monitor] Failed to mount live trajectory item: {e}")

                # Maintain windowed UI using counter (avoids expensive DOM queries)
                if self.trajectory_widget_count > MAX_TRAJECTORY_WIDGETS:
                    if not self._has_omitted_hint:
                        # First overflow: remove oldest widget, add hint
                        first_child = scroll.children[0] if scroll.children else None
                        if first_child:
                            first_child.remove()
                            self.trajectory_widget_count -= 1
                        scroll.mount(Static("... (Earlier history omitted)", classes="omitted-hint"), before=0)
                        self._has_omitted_hint = True
                    else:
                        # Subsequent overflow: remove first real item (after hint)
                        if len(scroll.children) > 1:
                            scroll.children[1].remove()
                            self.trajectory_widget_count -= 1

                # Smart Scroll: Apply
                if at_bottom:
                    scroll.scroll_end(animate=False)

        except Exception as e:
            Logger.error(f"[Monitor] Error processing log line: {e}")

    @on(Input.Submitted, "#intervention-input")
    def on_submit_intervention(self, event: Input.Submitted):
        cmd = event.value.strip()
        if not cmd or not self.selected_agent: return
        event.input.value = ""
        self.send_to_mailbox(self.selected_agent, cmd)

    def send_to_mailbox(self, agent_name: str, content: str):
        """
        Send a message to agent's mailbox using queue mode.
        Messages are stored as a list to prevent loss.
        """
        mailbox_dir = os.path.join(Config.BLACKBOARD_ROOT, "mailboxes")
        os.makedirs(mailbox_dir, exist_ok=True)
        mailbox_path = os.path.join(mailbox_dir, f"{agent_name}.json")
        
        new_message = {
            "timestamp": time.time(),
            "role": "user",
            "content": content,
            "status": "unread"
        }
        
        try:
            # Use file lock for safe concurrent access
            from src.utils.file_lock import file_lock
            
            with file_lock(mailbox_path, 'r+', fcntl.LOCK_EX, timeout=5) as fd:
                if fd is None:
                    Logger.error(f"[Monitor] Failed to acquire lock for mailbox {agent_name}")
                    return
                
                # Read existing messages
                content_str = fd.read()
                try:
                    messages = json.loads(content_str) if content_str else []
                    # Ensure it's a list
                    if not isinstance(messages, list):
                        messages = []
                except json.JSONDecodeError:
                    messages = []
                
                # Append new message
                messages.append(new_message)
                
                # Write back
                fd.seek(0)
                json.dump(messages, fd, indent=2, ensure_ascii=False)
                fd.truncate()
                
            Logger.info(f"[Monitor] Message sent to {agent_name}: {content[:50]}...")
        except Exception as e:
            Logger.error(f"[Monitor] Failed to write mailbox for {agent_name}: {e}")
