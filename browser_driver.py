"""
Browser Driver - WebSocket server for Chrome CDP Bridge extension.
Manages browser automation via JS execution in the user's browser.
"""

import json
import os
import subprocess
import threading
import time
import uuid
import queue
import shutil
from pathlib import Path
from typing import Any, Optional

try:
    from simple_websocket_server import WebSocketServer, WebSocket
    HAS_WS = True
except ImportError:
    HAS_WS = False


class _Session:
    """Represents a connected browser tab."""

    def __init__(self, session_id: str, info: dict, client=None):
        self.id = session_id
        self.info = info
        self.connect_at = time.time()
        self.disconnect_at: Optional[float] = None
        self.ws_client = client

    @property
    def url(self) -> str:
        return self.info.get('url', '')

    @property
    def title(self) -> str:
        return self.info.get('title', '')

    def is_active(self) -> bool:
        return self.disconnect_at is None

    def mark_disconnected(self):
        if self.is_active():
            self.disconnect_at = time.time()

    def reconnect(self, client, info):
        self.info = info
        self.ws_client = client
        self.connect_at = time.time()
        self.disconnect_at = None


class BrowserDriver:
    """
    WebSocket server that Chrome CDP Bridge extension connects to.
    Provides JS execution in the user's browser tabs.
    """

    def __init__(self, host: str = '127.0.0.1', port: int = 18765):
        self.host = host
        self.port = port
        self.sessions: dict[str, _Session] = {}
        self.results: dict[str, dict] = {}
        self.acks: dict[str, bool] = {}
        self.default_session_id: Optional[str] = None
        self.latest_session_id: Optional[str] = None
        self._server = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._status_callbacks: list = []
        self._ws_clients: set = set()  # Track connected WebSocket clients
        self._exec_lock = threading.Lock()  # Serialize JS execution for multi-worker safety

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def connected_tabs(self) -> list[dict]:
        """Return list of currently connected browser tabs."""
        return [
            {'id': s.id, 'url': s.url, 'title': s.title}
            for s in self.sessions.values() if s.is_active()
        ]

    @property
    def has_connection(self) -> bool:
        return bool(self._ws_clients)

    @property
    def has_tabs(self) -> bool:
        return any(s.is_active() for s in self.sessions.values())

    def on_status_change(self, callback):
        """Register a callback for connection status changes."""
        self._status_callbacks.append(callback)

    def _notify_status(self):
        for cb in self._status_callbacks:
            try:
                cb(self.has_connection)
            except Exception:
                pass

    def start(self):
        """Start the WebSocket server in a background thread."""
        if not HAS_WS:
            raise RuntimeError(
                "simple_websocket_server 未安装。请运行: pip install simple-websocket-server"
            )
        if self._running:
            return
        self._running = True
        driver = self

        class _Handler(WebSocket):
            def handle(self):
                try:
                    data = json.loads(self.data)
                    dtype = data.get('type', '')
                    if dtype == 'ext_ready' or dtype == 'tabs_update':
                        tabs = data.get('tabs', [])
                        current_ids = {str(t['id']) for t in tabs}
                        # Mark disconnected tabs
                        for sid in list(driver.sessions.keys()):
                            if sid not in current_ids and driver.sessions[sid].ws_client == self:
                                driver.sessions[sid].mark_disconnected()
                        # Register/update tabs
                        for tab in tabs:
                            sid = str(tab['id'])
                            info = {'url': tab.get('url', ''), 'title': tab.get('title', '')}
                            sess = driver.sessions.get(sid)
                            if sess and sess.is_active():
                                sess.info = info
                            else:
                                driver.sessions[sid] = _Session(sid, info, self)
                        driver.latest_session_id = str(tabs[0]['id']) if tabs else None
                        if driver.default_session_id is None:
                            driver.default_session_id = driver.latest_session_id
                        driver._notify_status()
                    elif dtype == 'ack':
                        driver.acks[data.get('id', '')] = True
                    elif dtype == 'result':
                        driver.results[data.get('id')] = {
                            'success': True, 'data': data.get('result'),
                            'newTabs': data.get('newTabs', []),
                        }
                    elif dtype == 'error':
                        driver.results[data.get('id')] = {
                            'success': False, 'data': data.get('error'),
                            'newTabs': data.get('newTabs', []),
                        }
                    elif dtype == 'ping':
                        pass  # keepalive
                except Exception as e:
                    print(f"[BrowserDriver] Error: {e}")

            def connected(self):
                driver._ws_clients.add(self)
                driver._notify_status()

            def handle_close(self):
                driver._ws_clients.discard(self)
                for s in driver.sessions.values():
                    if s.ws_client == self:
                        s.mark_disconnected()
                driver._notify_status()

        self._server = WebSocketServer(self.host, self.port, _Handler)
        def _serve():
            try:
                self._server.serve_forever()
            except Exception:
                pass  # Socket closed during shutdown is expected

        self._thread = threading.Thread(target=_serve, daemon=True)
        self._thread.start()
        print(f"[BrowserDriver] WebSocket server running on ws://{self.host}:{self.port}")

    def stop(self):
        """Stop the WebSocket server."""
        self._running = False
        if self._server:
            try:
                self._server.close()
            except Exception:
                pass
        self.sessions.clear()
        self._ws_clients.clear()
        self._notify_status()

    def launch_browser(self, url: str = None, timeout: float = 30) -> dict:
        """Launch Chrome browser and wait for the extension to connect.

        Args:
            url: Optional URL to open after launch.
            timeout: Max seconds to wait for extension connection.

        Returns:
            {'launched': True, 'already_running': bool, 'tabs': [...]}
        """
        import platform

        # Check if extension is already connected
        already_running = bool(self._ws_clients)

        if not already_running:
            # Find Chrome executable
            chrome_path = None
            system = platform.system()

            if system == 'Windows':
                candidates = [
                    Path(os.environ.get('PROGRAMFILES', '')) / 'Google' / 'Chrome' / 'Application' / 'chrome.exe',
                    Path(os.environ.get('PROGRAMFILES(X86)', '')) / 'Google' / 'Chrome' / 'Application' / 'chrome.exe',
                    Path(os.environ.get('LOCALAPPDATA', '')) / 'Google' / 'Chrome' / 'Application' / 'chrome.exe',
                ]
                for p in candidates:
                    if p.exists():
                        chrome_path = str(p)
                        break
            elif system == 'Darwin':
                candidate = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
                if candidate.exists():
                    chrome_path = str(candidate)
            else:  # Linux
                for name in ['google-chrome', 'google-chrome-stable', 'chromium-browser', 'chromium']:
                    found = shutil.which(name)
                    if found:
                        chrome_path = found
                        break

            if not chrome_path:
                raise RuntimeError(
                    "找不到 Chrome 浏览器。请确认已安装 Google Chrome。"
                )

            # Launch Chrome
            args = [chrome_path]
            if url:
                args.append(url)
            try:
                subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                raise RuntimeError(f"启动 Chrome 失败: {e}")

            print(f"[BrowserDriver] Chrome launched: {chrome_path}")

        # Wait for extension to connect (or if already connected, just wait for tabs)
        start = time.time()
        while time.time() - start < timeout:
            if self._ws_clients:
                # Extension connected, now wait for at least one tab if url was specified
                if url:
                    # Wait a bit for the tab to register
                    tab_deadline = time.time() + 5
                    while time.time() < tab_deadline:
                        if self.has_tabs:
                            break
                        time.sleep(0.3)
                return {
                    'launched': True,
                    'already_running': already_running,
                    'chrome_path': chrome_path if not already_running else None,
                    'tabs': self.connected_tabs,
                }
            time.sleep(0.5)

        raise TimeoutError(
            f"Chrome 已启动但扩展未连接 ({timeout}s)。"
            "请确认 TMWD CDP Bridge 扩展已安装并启用。"
        )

    def open_tab(self, url: str, timeout: float = 15) -> dict:
        """Open a new tab in the browser via the extension.

        Returns {'id': tab_id, 'url': ..., 'title': ...} on success.
        """
        if not self._running:
            raise RuntimeError("BrowserDriver 未启动")
        if not self._ws_clients:
            raise RuntimeError("没有已连接的浏览器扩展")

        # Pick any connected client to send the command
        client = next(iter(self._ws_clients))
        exec_id = str(uuid.uuid4())
        payload = json.dumps({
            'id': exec_id,
            'tabId': 0,  # Not used for tabs.create
            'code': {'cmd': 'tabs', 'method': 'create', 'url': url},
        })

        # Snapshot existing session IDs before opening
        before_ids = set(self.sessions.keys())

        client.send_message(payload)

        start = time.time()
        while exec_id not in self.results:
            time.sleep(0.1)
            if time.time() - start > timeout:
                raise TimeoutError(f"打开标签页超时 ({timeout}s)")

        result = self.results.pop(exec_id)
        if not result['success']:
            raise RuntimeError(str(result['data']))

        tab_info = result['data']

        # Wait briefly for the extension to send tabs_update with the new tab
        deadline = time.time() + 3
        tab_id = str(tab_info.get('id', ''))
        while time.time() < deadline:
            if tab_id in self.sessions and self.sessions[tab_id].is_active():
                break
            new_ids = set(self.sessions.keys()) - before_ids
            if new_ids:
                tab_id = new_ids.pop()
                break
            time.sleep(0.2)

        # Set as default session so web_scan targets it automatically
        if tab_id and tab_id in self.sessions:
            self.default_session_id = tab_id

        return tab_info

    def set_default_session(self, session_id: str) -> bool:
        """Set the default tab to execute JS in."""
        if session_id in self.sessions and self.sessions[session_id].is_active():
            self.default_session_id = session_id
            return True
        return False

    def find_session(self, url_pattern: str) -> list[tuple[str, dict]]:
        """Find sessions whose URL contains the pattern."""
        results = []
        for s in self.sessions.values():
            if s.is_active() and url_pattern in s.url:
                results.append((s.id, {'url': s.url, 'title': s.title}))
        return results

    def execute_js(self, code: str, timeout: float = 15, session_id: str = None) -> dict:
        """
        Execute JavaScript code in a browser tab.
        Returns {'data': result} on success or raises on error.
        Thread-safe: concurrent callers queue behind _exec_lock.
        """
        with self._exec_lock:
            if not self._running:
                raise RuntimeError("BrowserDriver 未启动")
            sid = session_id or self.default_session_id
            if not sid:
                raise RuntimeError("没有已连接的浏览器标签页")
            session = self.sessions.get(sid)
            if not session or not session.is_active():
                # Try latest
                if self.latest_session_id:
                    sid = self.latest_session_id
                    session = self.sessions.get(sid)
                if not session or not session.is_active():
                    raise RuntimeError(f"标签页 {sid} 未连接")

            exec_id = str(uuid.uuid4())
            payload = json.dumps({'id': exec_id, 'tabId': int(sid), 'code': code})

            session.ws_client.send_message(payload)

            start = time.time()
            acked = False
            while exec_id not in self.results:
                time.sleep(0.1)
                if not acked and exec_id in self.acks:
                    acked = True
                    start = time.time()  # Reset timeout after ack
                if time.time() - start > timeout:
                    if acked:
                        raise TimeoutError(f"JS 执行超时 ({timeout}s, 已收到 ACK)")
                    raise TimeoutError(f"JS 执行超时 ({timeout}s, 未收到 ACK)")

            result = self.results.pop(exec_id)
            self.acks.pop(exec_id, None)
            if not result['success']:
                err = result['data']
                if isinstance(err, dict):
                    raise RuntimeError(f"{err.get('name', 'Error')}: {err.get('message', str(err))}")
                raise RuntimeError(str(err))
            return {'data': result['data'], 'newTabs': result.get('newTabs', [])}


# Singleton driver instance
_driver: Optional[BrowserDriver] = None


def get_browser_driver() -> BrowserDriver:
    """Get or create the singleton BrowserDriver."""
    global _driver
    if _driver is None:
        _driver = BrowserDriver()
    return _driver


def start_browser_driver() -> bool:
    """Start the browser driver. Returns True if started successfully."""
    try:
        driver = get_browser_driver()
        driver.start()
        return True
    except RuntimeError:
        return False


def stop_browser_driver():
    """Stop the browser driver."""
    global _driver
    if _driver:
        _driver.stop()
        _driver = None
