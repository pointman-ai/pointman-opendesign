"""Open Design on this node (extensions/opendesign/extension.toml): a design from a brief, made by
Claude Code under Open Design's own daemon, which this extension starts and keeps to itself.

- **Its own daemon:** data in this extension's state folder, on a port of its own, so it never
  touches another Open Design. Its runs' sessions live under the node's extensions folder, which
  the node leaves out of the sessions it tells the board about.
- **The node decides how Claude Code runs:** Open Design asks for bypassPermissions; a shim in
  front of `claude` (CLAUDE_BIN) swaps in the node's own permission mode (node-allow.toml's
  `permission_mode`, as for wakes, else auto) and loads no MCP servers, so the board's tools and
  token stay out.
- **A design is a project:** a brief starts a run; the same project again revises it, and the node
  publishes it at the same path, so a revision is a new version of the same card.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

from pointman_extension import Extension, Retry

ext = Extension()
HERE = Path(__file__).resolve().parent
MODES = ("auto", "default", "acceptEdits", "plan")
CLAUDE_PATHS = ("/opt/homebrew/bin/claude", "~/.local/bin/claude", "/usr/local/bin/claude", "~/.claude/local/claude")

SHIM = '''#!{python}
"""Claude Code for Open Design's runs on this node (made by the opendesign extension): the node's
permission mode, whatever Open Design asks for, and no MCP servers."""
import os, sys
REAL, MODE = {real!r}, {mode!r}
args, out, i = sys.argv[1:], [], 0
while i < len(args):
    a = args[i]
    if a == "--permission-mode":
        i += 2
        continue
    if a.startswith("--permission-mode=") or a in ("--dangerously-skip-permissions",
                                                   "--allow-dangerously-skip-permissions"):
        i += 1
        continue
    out.append(a)
    i += 1
if "-p" in out and "--help" not in out and "--version" not in out:
    out += ["--permission-mode", MODE, "--strict-mcp-config"]
env = {{k: v for k, v in os.environ.items() if not k.startswith(("MB_", "DAILIES_"))}}
os.execve(REAL, [REAL, *out], env)
'''


def _http(method: str, url: str, body: dict | None = None, timeout: float = 30) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def permission_mode() -> str:
    """The node's own choice for sessions it runs (node-allow.toml, as for wakes), never core's."""
    path = Path(os.environ.get("MB_NODE_ALLOW") or "~/.config/message-board/node-allow.toml").expanduser()
    try:
        mode = tomllib.loads(path.read_text(encoding="utf-8")).get("permission_mode")
    except (OSError, ValueError):
        mode = None
    return mode if mode in MODES else "auto"


def real_claude(skip: Path) -> str:
    found = [shutil.which("claude")] + [str(Path(p).expanduser()) for p in CLAUDE_PATHS]
    for c in found:
        if c and Path(c).is_file() and os.access(c, os.X_OK) and Path(c).resolve().parent != skip.resolve():
            return c
    raise Retry("Claude Code isn't installed on this machine (no claude)")


class Daemon:
    """Open Design's daemon, started on first use and kept for the extension's life."""

    def __init__(self):
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.base: str | None = None

    @property
    def state(self) -> Path:
        return Path(ext.node.get("state_dir") or ".")

    @property
    def data(self) -> Path:
        return self.state / "od"

    def root(self) -> Path:
        return Path(str(ext.settings.get("path") or "~/open-design")).expanduser()

    def healthy(self) -> dict | None:
        if not self.base or self.proc is None or self.proc.poll() is not None:
            return None
        try:
            return _http("GET", f"{self.base}/api/health", timeout=5)
        except (OSError, ValueError):
            return None

    def ensure(self) -> str:
        with self.lock:
            if self.healthy() is None:
                self.start()
            return self.base

    def shim(self) -> Path:
        bin_dir = self.state / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        path = bin_dir / "claude"
        path.write_text(SHIM.format(python=sys.executable, real=real_claude(bin_dir), mode=permission_mode()),
                        encoding="utf-8")
        path.chmod(0o700)
        return path

    def start(self) -> None:
        root = self.root()
        cli = root / "apps" / "daemon" / "dist" / "cli.js"
        if not cli.exists():
            self.status(False, f"no built Open Design at {root}")
            raise Retry(f"Open Design isn't built at {root} (no apps/daemon/dist/cli.js): set its path in "
                        "this machine's extension settings")
        node = shutil.which(os.path.expanduser(str(ext.settings.get("node") or "node")))
        if not node:
            raise Retry(f"no node at {ext.settings.get('node')!r} to run Open Design with")
        self.stop()
        self._stop_stale()
        self.data.mkdir(parents=True, exist_ok=True)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        env = {k: v for k, v in os.environ.items() if not k.startswith(("MB_", "DAILIES_"))}
        env.update(OD_DATA_DIR=str(self.data), OD_PORT=str(port), CLAUDE_BIN=str(self.shim()))
        with open(self.state / "daemon.log", "ab") as logf:
            self.proc = subprocess.Popen([node, str(cli), "--no-open", "--port", str(port)], cwd=root, env=env,
                                         stdin=subprocess.DEVNULL, stdout=logf, stderr=logf, start_new_session=True)
        (self.state / "daemon.pid").write_text(str(self.proc.pid))
        self.base = f"http://127.0.0.1:{port}"
        end = time.monotonic() + 90
        while time.monotonic() < end:
            if (h := self.healthy()) is not None:
                self.status(True, None, h.get("version"))
                return
            if self.proc.poll() is not None:
                break
            time.sleep(0.5)
        self.stop()
        self.status(False, "its daemon didn't start (see daemon.log)")
        raise Retry("Open Design's daemon didn't start: see daemon.log in the extension's state folder")

    def status(self, running: bool, reason: str | None, version: str | None = None) -> None:
        ext.event("opendesign.status", {"running": running, "path": str(self.root()), "version": version,
                                        **({"reason": reason} if reason else {})})

    def _stop_stale(self) -> None:
        """A daemon left by this extension's last process (it was killed before it could stop it)."""
        pidf = self.state / "daemon.pid"
        try:
            pid = int(pidf.read_text())
            os.killpg(pid, signal.SIGTERM)
            time.sleep(1)
        except (OSError, ValueError):
            pass
        pidf.unlink(missing_ok=True)

    def stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)          # the daemon and the runs it started
                proc.wait(10)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass
        (self.state / "daemon.pid").unlink(missing_ok=True)


daemon = Daemon()


def new_project(brief: str) -> str:
    words = re.findall(r"[a-z0-9]+", brief.lower())[:4]
    return "-".join(words + [secrets.token_hex(2)])[:48] or f"design-{secrets.token_hex(3)}"


def ensure_project(base: str, project: str, design_md: str | None) -> bool:
    """The project in Open Design, made if it's new; a new DESIGN.md replaces its instructions.
    True if it was there already (a revision)."""
    try:
        _http("GET", f"{base}/api/projects/{project}")
        if design_md:
            _http("PATCH", f"{base}/api/projects/{project}", {"customInstructions": design_md})
        return True
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    _http("POST", f"{base}/api/projects", {"id": project, "name": project, "customInstructions": design_md or "",
                                           "skipDiscoveryBrief": True})
    return False


def follow(base: str, run_id: str, job, current: list[str]) -> dict:
    """The run's events until it ends (and the runs it hands on to): progress on the way, its end.
    `current` holds the run under way, for cancelling it."""
    started, events, last = time.monotonic(), 0, 0.0
    while True:
        current[0] = run_id
        req = urllib.request.Request(f"{base}/api/runs/{run_id}/events", headers={"accept": "text/event-stream"})
        end, nxt = None, None
        with urllib.request.urlopen(req, timeout=3600) as r:
            name, data = "message", []
            for raw in r:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if line.startswith("event: "):
                    name = line[7:]
                elif line.startswith("data: "):
                    data.append(line[6:])
                elif line == "" and (data or name != "message"):
                    events += 1
                    if name == "end":
                        try:
                            end = json.loads("\n".join(data))
                        except ValueError:
                            end = {}
                        task = end.get("strategyTask") or {}
                        if task and task.get("terminal") is not True:
                            cand = task.get("activeRunId") if task.get("activeRunId") != run_id else task.get("nextRunId")
                            nxt = cand if isinstance(cand, str) and cand and cand != run_id else None
                        break
                    now = time.monotonic()
                    if now - last >= 10:
                        last = now
                        mins = (now - started) / 60
                        # a run takes about 15 min: a rough fraction that never claims it's done
                        job.progress(min(0.95, mins / 16), "designing", minutes=round(mins, 1), events=events)
                    name, data = "message", []
        if nxt is None:
            return end or {"status": "failed", "failureDetail": "the run's events stopped before it ended"}
        run_id = nxt


def still(page: Path, out: Path) -> Path | None:
    """A WebKit still of the page, scripts run (macOS); none elsewhere, and the node makes its own thumb."""
    if sys.platform != "darwin":
        return None
    snap = daemon.state / "bin" / "snap"
    src = HERE / "snap.swift"
    if not snap.exists() or snap.stat().st_mtime < src.stat().st_mtime:
        snap.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["swiftc", "-O", str(src), "-o", str(snap)], capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            ext.log(f"snap didn't build: {r.stderr[-500:]}")
            return None
    width = int(ext.settings.get("still_width") or 1440)
    r = subprocess.run([str(snap), str(page), str(out), str(width), str(width * 10 // 16), "3"],
                       capture_output=True, text=True, timeout=120)
    return out if r.returncode == 0 and out.exists() else None


@ext.command("opendesign.make")
def make(job):
    p = job.params
    started = time.monotonic()
    job.progress(0.0, "starting Open Design")
    base = daemon.ensure()
    project = p.get("project") or new_project(p["brief"])
    revised = ensure_project(base, project, p.get("design_md"))
    plugin = str(ext.settings.get("plugin") or "od-default")
    inputs = {"prompt": p["brief"]}
    _http("POST", f"{base}/api/plugins/{plugin}/apply", {"inputs": inputs, "grantCaps": [], "projectId": project})
    run = _http("POST", f"{base}/api/runs", {"projectId": project, "pluginId": plugin, "pluginInputs": inputs,
                                             "grantCaps": [], "agentId": "claude"})
    run_id = run.get("runId") or run.get("id")
    if not run_id:
        raise RuntimeError(f"Open Design didn't start a run: {json.dumps(run)[:300]}")
    current, done = [run_id], threading.Event()

    def cancel_when_asked() -> None:            # the events can go quiet for minutes: watch apart from them
        while not done.wait(0.5):
            if job.cancelled:
                try:
                    _http("POST", f"{base}/api/runs/{current[0]}/cancel", {})
                except (OSError, ValueError):
                    pass
                return

    threading.Thread(target=cancel_when_asked, name="cancel-watch", daemon=True).start()
    try:
        end = follow(base, run_id, job, current)
    finally:
        done.set()
    job.check()
    if end.get("status") != "succeeded":
        why = end.get("failureDetail") or end.get("failureCategory") or end.get("status") or "it didn't finish"
        raise RuntimeError(f"Open Design's run ended {end.get('status')}: {why}")
    folder = daemon.data / "projects" / project
    pages = [folder / a for a in end.get("artifactPaths") or [] if str(a).endswith(".html")]
    page = next((x for x in pages if x.is_file()), folder / "index.html")
    if not page.is_file():
        raise RuntimeError(f"the run made no page in {folder}")
    # Open Design names the page as it likes (hello-card.html): one name on the board, so a revision
    # is the same card
    meta = {"file": project, "project": project, "revised": revised, "publish_as": "index.html"}
    job.output(page, kind="page", meta=meta)
    picture = still(page, job.work / f"{project}.png") if ext.settings.get("still", True) else None
    if picture is not None:
        job.output(picture, kind="image", role="poster", meta=meta)
    return {"project": project, "run": run_id, "revised": revised, "seconds": round(time.monotonic() - started)}


@ext.on_start
def _status():
    root = daemon.root()
    found = (root / "apps" / "daemon" / "dist" / "cli.js").exists()
    daemon.status(False, None if found else f"no built Open Design at {root}")


try:
    ext.run()
finally:
    daemon.stop()
