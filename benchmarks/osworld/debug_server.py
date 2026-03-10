"""Live debug server for OSWorld benchmark runs.

Task detail page is a replica of the DETM liveui popup:
  Left:  event feed (thoughts, tool calls, results)
  Right: screenshot viewer with scrubber

Usage:
    python3 benchmarks/osworld/debug_server.py [--port 8888] [--results-dir ...]

Then open http://localhost:8888 in your browser.
"""

import argparse
import json
import os
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn
from pathlib import Path

OSWORLD_DIR = os.environ.get("OSWORLD_DIR", "/home/alex/OSWorld")
DEFAULT_RESULTS = os.path.join(OSWORLD_DIR, "results/pyautogui/screenshot/detm-gemini-uitars")


def load_task_config(domain, example_id):
    config_file = os.path.join(OSWORLD_DIR, f"evaluation_examples/examples/{domain}/{example_id}.json")
    if os.path.exists(config_file):
        with open(config_file) as f:
            return json.load(f)
    return None


def _esc(s):
    if s is None:
        return ""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# ── CSS: exact copy of DETM dashboard lsv-* styles with vars resolved ──

_LSV_CSS = """
:root {
  --bg:        #0d1117;
  --bg-alt:    #161b22;
  --bg-hover:  #1c2128;
  --border:    #30363d;
  --text:      #c9d1d9;
  --text-dim:  #8b949e;
  --blue:      #58a6ff;
  --green:     #3fb950;
  --red:       #f85149;
  --yellow:    #d29922;
  --gray:      #484f58;
  --purple:    #bc8cff;
  --font-mono: 'SF Mono', 'Cascadia Code', 'Fira Code', 'JetBrains Mono', Consolas, monospace;
}
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html, body {
  height: 100%; background: var(--bg); color: var(--text);
  font-family: var(--font-mono); font-size: 13px; line-height: 1.5; overflow: hidden;
}

/* Header — same as lsv-header */
.lsv-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 16px; border-bottom: 1px solid var(--border);
  background: var(--bg); flex-shrink: 0;
}
.lsv-header-left { display: flex; align-items: center; gap: 10px; }
.lsv-title { font-size: 13px; font-weight: 700; color: #38bdc1; letter-spacing: 0.4px; }
.lsv-session-id { font-size: 11px; color: var(--text-dim); }
.lsv-back { color: var(--blue); text-decoration: none; font-size: 12px; }
.lsv-back:hover { text-decoration: underline; }
.lsv-score {
  font-size: 12px; font-weight: 600; padding: 2px 10px; border-radius: 12px;
}
.lsv-score.pass { background: rgba(63,185,80,0.15); color: var(--green); }
.lsv-score.fail { background: rgba(248,81,73,0.15); color: var(--red); }
.lsv-score.running { background: rgba(210,153,34,0.15); color: var(--yellow); }

/* Instruction bar */
.lsv-instruction-bar {
  padding: 8px 16px; border-bottom: 1px solid var(--border);
  display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px;
  font-size: 11px; flex-shrink: 0; background: var(--bg);
}
.lsv-instr-label { color: var(--text-dim); text-transform: uppercase; font-size: 10px; letter-spacing: 0.5px; }
.lsv-instr-text { color: var(--text); max-width: 800px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* Two-column body */
.lsv-body { display: flex; flex: 1; overflow: hidden; gap: 0; }

/* Event feed (left 42%) */
.lsv-feed {
  width: 42%; border-right: 1px solid var(--border);
  overflow-y: auto; padding: 8px 0; display: flex; flex-direction: column; gap: 1px;
}
.lsv-event {
  display: flex; align-items: flex-start; gap: 8px;
  padding: 4px 12px; cursor: pointer; transition: background 0.1s;
}
.lsv-event:hover { background: var(--bg-hover); }
.lsv-event-active { background: rgba(255,200,50,0.13); outline: 1px solid rgba(255,200,50,0.4); }

.lsv-sender {
  flex-shrink: 0; display: inline-block; padding: 1px 5px; border-radius: 3px;
  font-size: 10px; font-weight: 600; letter-spacing: 0.3px; white-space: nowrap;
}
.lsv-sender.sender-gui    { background: rgba(188,140,255,0.15); color: var(--purple); }
.lsv-sender.sender-vision { background: rgba(63,185,80,0.15);   color: var(--green); }
.lsv-sender.sender-wait   { background: rgba(210,153,34,0.15);  color: var(--yellow); }
.lsv-sender.sender-cli    { background: rgba(56,189,193,0.15);  color: #38bdc1; }
.lsv-sender.sender-tool   { background: rgba(88,166,255,0.15);  color: var(--blue); }
.lsv-sender.sender-agent  { background: rgba(72,79,88,0.2);     color: var(--text-dim); }
.lsv-sender.sender-error  { background: rgba(248,81,73,0.15);   color: var(--red); }

.lsv-event-text {
  font-size: 11px; color: var(--text); white-space: pre-wrap;
  word-break: break-word; flex: 1; min-width: 0;
}
.lsv-event-text.lsv-muted { color: var(--text-dim); }

.lsv-sep { border-bottom: 1px solid var(--border); margin: 4px 12px; }

/* Frame viewer (right 58%) */
.lsv-viewer {
  flex: 1; display: flex; flex-direction: column; background: #000; overflow: hidden;
}
.lsv-frame-box {
  flex: 1; display: flex; align-items: center; justify-content: center;
  overflow: hidden; background: #000;
}
.lsv-frame-box img { max-width: 100%; max-height: 100%; object-fit: contain; display: block; }
.lsv-scrubber-row {
  display: flex; align-items: center; gap: 8px; padding: 8px 12px;
  background: var(--bg-alt); border-top: 1px solid var(--border); flex-shrink: 0;
}
.lsv-scrubber { flex: 1; accent-color: #38bdc1; cursor: pointer; height: 4px; }
.lsv-nav-btn {
  background: none; border: 1px solid var(--border); border-radius: 3px;
  color: var(--text-dim); font-size: 11px; padding: 2px 7px; cursor: pointer;
  transition: color 0.15s, border-color 0.15s;
}
.lsv-nav-btn:hover { color: var(--text); border-color: var(--text-dim); }
.lsv-frame-label { font-size: 11px; color: var(--text-dim); min-width: 56px; text-align: right; }
.lsv-frame-phase { font-size: 10px; color: var(--blue); font-weight: 600; min-width: 80px; }
"""


class DebugHandler(SimpleHTTPRequestHandler):
    results_dir = DEFAULT_RESULTS

    def do_GET(self):
        # Snapshot routes: /snap/name/... serves from _snapshots/name/
        if self.path.startswith("/snap/"):
            rest = self.path[6:]  # "name/..." or "name"
            parts = rest.split("/", 1)
            snap_name = parts[0]
            snap_dir = os.path.join(self.results_dir, "_snapshots", snap_name)
            if not os.path.isdir(snap_dir):
                self._404()
                return
            sub_path = "/" + parts[1] if len(parts) > 1 else "/"
            # Re-dispatch with overridden results_dir
            self._dispatch(sub_path, snap_dir)
            return

        self._dispatch(self.path, self.results_dir)

    def _dispatch(self, path, results_dir):
        if path == "/" or path == "/index":
            self._serve_index(results_dir)
        elif path.startswith("/task/"):
            parts = path[6:].split("/")
            if len(parts) == 3:
                self._serve_task(parts[1], parts[2], model=parts[0], results_dir=results_dir)
            elif len(parts) == 2:
                self._serve_task(parts[0], parts[1], results_dir=results_dir)
            else:
                self._404()
        elif path.startswith("/img/"):
            self._serve_image(path[5:], results_dir)
        else:
            self._404()

    def _scan_tasks(self, results_dir):
        """Scan a results directory for tasks. Returns list of task dicts."""
        results_base = Path(results_dir)
        tasks = []
        if not results_base.exists():
            return tasks
        for top_dir in sorted(results_base.iterdir()):
            if not top_dir.is_dir() or top_dir.name.startswith("_"):
                continue
            is_model_dir = top_dir.name.startswith("detm-")
            if is_model_dir:
                model_name = top_dir.name
                domain_dirs = sorted(top_dir.iterdir())
            else:
                model_name = None
                domain_dirs = [top_dir]
            for domain_dir in domain_dirs:
                if not domain_dir.is_dir():
                    continue
                domain = domain_dir.name
                for example_dir in sorted(domain_dir.iterdir()):
                    if not example_dir.is_dir():
                        continue
                    example_id = example_dir.name
                    result_file = example_dir / "result.txt"
                    traj_file = example_dir / "traj.jsonl"
                    score = None
                    if result_file.exists():
                        try:
                            score = float(result_file.read_text().strip())
                        except ValueError:
                            score = 0.0
                    n_steps = 0
                    if traj_file.exists():
                        with open(traj_file) as f:
                            n_steps = sum(1 for line in f if line.strip())
                    # Read checkpoint scores
                    checkpoints = {}
                    for ckpt_file in example_dir.glob("result_at_*.txt"):
                        try:
                            ckpt_step = int(ckpt_file.stem.split("_")[-1])
                            checkpoints[ckpt_step] = float(ckpt_file.read_text().strip())
                        except (ValueError, IndexError):
                            pass
                    # Use most recent file modification time for sorting
                    mtime = 0
                    if result_file.exists():
                        mtime = result_file.stat().st_mtime
                    elif traj_file.exists():
                        mtime = traj_file.stat().st_mtime
                    config = load_task_config(domain, example_id)
                    instruction = config["instruction"][:100] if config else "?"
                    status = "running" if score is None and n_steps > 0 else ("done" if score is not None else "pending")
                    tasks.append({"domain": domain, "id": example_id, "score": score,
                                  "steps": n_steps, "instruction": instruction, "status": status,
                                  "model": model_name, "mtime": mtime, "checkpoints": checkpoints})
        # Sort by modification time descending (newest first)
        tasks.sort(key=lambda t: t["mtime"], reverse=True)
        return tasks

    def _serve_index(self, results_dir=None):
        results_dir = results_dir or self.results_dir
        # Determine if we're viewing a snapshot
        is_snapshot = results_dir != self.results_dir
        snap_prefix = ""
        if is_snapshot:
            # Extract snapshot name from path
            snap_name = Path(results_dir).name
            snap_prefix = f"/snap/{snap_name}"

        tasks = self._scan_tasks(results_dir)

        rows = ""
        for t in tasks:
            if t["score"] is not None and t["score"] > 0:
                score_html = f'<span style="color:#3fb950;font-weight:600">{t["score"]:.1f}</span>'
            elif t["score"] is not None:
                score_html = f'<span style="color:#f85149;font-weight:600">{t["score"]:.1f}</span>'
            elif t["status"] == "running":
                score_html = '<span style="color:#d29922">running</span>'
            else:
                score_html = '<span style="color:#484f58">--</span>'
            model = t.get("model")
            if model:
                link = f'{snap_prefix}/task/{_esc(model)}/{_esc(t["domain"])}/{_esc(t["id"])}'
                model_short = model.replace("detm-", "").replace("-uitars", "")
            else:
                link = f'{snap_prefix}/task/{_esc(t["domain"])}/{_esc(t["id"])}'
                model_short = ""
            # Checkpoint score cells
            ckpts = t.get("checkpoints", {})
            ckpt_cells = ""
            for ck in [15, 50]:
                cv = ckpts.get(ck)
                if cv is not None and cv > 0:
                    ckpt_cells += f'<td><span style="color:#3fb950">{cv:.1f}</span></td>'
                elif cv is not None:
                    ckpt_cells += f'<td><span style="color:#f85149">{cv:.1f}</span></td>'
                else:
                    ckpt_cells += '<td><span style="color:#484f58">--</span></td>'
            rows += f'''<tr onclick="location.href='{link}'">
                <td>{_esc(model_short)}</td><td>{_esc(t["domain"])}</td>{ckpt_cells}<td>{score_html}</td>
                <td>{t["steps"]}</td><td class="instr">{_esc(t["instruction"])}</td></tr>'''

        passed = sum(1 for t in tasks if t["score"] and t["score"] > 0)
        total = len(tasks)
        done = sum(1 for t in tasks if t["score"] is not None)

        # Checkpoint summaries
        ckpt_summary = ""
        for ck in [15, 50]:
            ck_tasks = [t for t in tasks if ck in t.get("checkpoints", {})]
            if ck_tasks:
                ck_pass = sum(1 for t in ck_tasks if t["checkpoints"][ck] > 0)
                ck_total = len(ck_tasks)
                ckpt_summary += f" &mdash; @{ck}: {ck_pass}/{ck_total} ({ck_pass/ck_total*100:.0f}%)"

        # Build snapshot links
        snap_dir = Path(self.results_dir) / "_snapshots"
        snap_links = ""
        if snap_dir.is_dir():
            snaps = sorted(snap_dir.iterdir())
            if snaps:
                snap_items = "".join(
                    f'<a href="/snap/{_esc(s.name)}/" style="color:var(--blue);margin-right:12px">{_esc(s.name)}</a>'
                    for s in snaps if s.is_dir()
                )
                snap_links = f'<div class="snapshots">Snapshots: {snap_items}</div>'

        title = f"OSWorld Debug — snapshot: {Path(results_dir).name}" if is_snapshot else "OSWorld Debug"
        back_link = '<a href="/" style="color:var(--blue);margin-right:12px">&larr; live</a>' if is_snapshot else ""

        html = f'''<!DOCTYPE html><html><head><meta charset="utf-8">
<title>{_esc(title)}</title>{"" if is_snapshot else '<meta http-equiv="refresh" content="5">'}
<style>
:root {{ --bg:#0d1117; --bg-alt:#161b22; --border:#30363d; --text:#c9d1d9; --text-dim:#8b949e;
         --blue:#58a6ff; --font-mono:'SF Mono','Cascadia Code','Fira Code',Consolas,monospace; }}
*,*::before,*::after {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ font-family:var(--font-mono); background:var(--bg); color:var(--text); padding:24px; font-size:13px; }}
h1 {{ font-size:18px; color:#f0f6fc; margin-bottom:6px; }}
.summary {{ color:var(--text-dim); margin-bottom:8px; font-size:13px; }}
.snapshots {{ color:var(--text-dim); margin-bottom:16px; font-size:12px; }}
table {{ width:100%; border-collapse:collapse; }}
th {{ text-align:left; color:var(--text-dim); font-size:11px; padding:8px 12px; border-bottom:1px solid var(--border);
      text-transform:uppercase; letter-spacing:0.5px; }}
td {{ padding:10px 12px; border-bottom:1px solid #21262d; font-size:13px; }}
tr {{ cursor:pointer; }} tr:hover {{ background:var(--bg-alt); }}
.instr {{ color:var(--text-dim); max-width:500px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
</style></head><body>
<h1>{back_link}{_esc(title)}</h1>
<div class="summary">{done}/{total} done &mdash; {passed}/{done if done else 1} passed ({passed/(done if done else 1)*100:.0f}%){ckpt_summary}</div>
{snap_links}
<table><tr><th>Model</th><th>Domain</th><th>@15</th><th>@50</th><th>Final</th><th>Steps</th><th>Instruction</th></tr>{rows}</table>
</body></html>'''
        self._respond(200, html)

    def _serve_task(self, domain, example_id, model=None, results_dir=None):
        """Liveui popup replica: two-column feed + frame viewer."""
        # Determine URL prefix for snapshots
        base = results_dir or self.results_dir
        url_prefix = ""
        if results_dir and results_dir != self.results_dir:
            snap_name = Path(results_dir).name
            url_prefix = f"/snap/{snap_name}"
        base = results_dir or self.results_dir
        if model:
            result_dir = Path(base) / model / domain / example_id
        else:
            result_dir = Path(base) / domain / example_id
        traj_file = result_dir / "traj.jsonl"
        result_file = result_dir / "result.txt"

        config = load_task_config(domain, example_id)
        instruction = config["instruction"] if config else "UNKNOWN"

        score_text = "running..."
        score_cls = "running"
        if result_file.exists():
            score_text = result_file.read_text().strip()
            score_cls = "pass" if score_text not in ("0", "0.0") else "fail"

        # Load trajectory
        steps = []
        if traj_file.exists():
            with open(traj_file) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        steps.append(json.loads(line))

        # Build frame list from screenshots + overlay/narrow/converge images
        screenshots = sorted(result_dir.glob("step_*.png"))
        overlays = sorted(result_dir.glob("step_*_overlay_*.jpg"))
        narrows = sorted(result_dir.glob("step_*_narrow_*.jpg"))
        converges = sorted(result_dir.glob("step_*_converge_*.jpg"))
        img_prefix = f"{model}/{domain}/{example_id}" if model else f"{domain}/{example_id}"
        frame_list = []
        for s in screenshots:
            name = s.stem  # step_1_before_20260308...
            parts = name.split("_")
            step_num = int(parts[1])
            phase = "before" if "before" in name else "after"
            rel = f"{img_prefix}/{s.name}"
            frame_list.append({"step": step_num, "phase": phase, "src": f"{url_prefix}/img/{rel}",
                               "label": f"Step {step_num} — {'screenshot before action' if phase == 'before' else 'screenshot after action'}"})
        for s in overlays:
            name = s.stem  # step_1_overlay_2
            parts = name.split("_")
            step_num = int(parts[1])
            round_num = int(parts[3])
            rel = f"{img_prefix}/{s.name}"
            frame_list.append({"step": step_num, "phase": f"overlay {round_num}", "src": f"{url_prefix}/img/{rel}",
                               "label": f"Step {step_num} — UI-TARS cursor placement (round {round_num})"})
        for s in narrows:
            name = s.stem  # step_1_narrow_0
            parts = name.split("_")
            step_num = int(parts[1])
            narrow_idx = int(parts[3])
            rel = f"{img_prefix}/{s.name}"
            frame_list.append({"step": step_num, "phase": f"narrow {narrow_idx}", "src": f"{url_prefix}/img/{rel}",
                               "label": f"Step {step_num} — iterative narrowing crop (round {narrow_idx})"})
        for s in converges:
            name = s.stem  # step_1_converge_0
            parts = name.split("_")
            step_num = int(parts[1])
            converge_idx = int(parts[3])
            rel = f"{img_prefix}/{s.name}"
            frame_list.append({"step": step_num, "phase": f"converge {converge_idx}", "src": f"{url_prefix}/img/{rel}",
                               "label": f"Step {step_num} — convergence check (round {converge_idx})"})
        # Sort by step number, then phase order:
        # before < narrow N < overlay N < converge N < after
        def _phase_sort_key(f):
            step = f["step"]
            p = f["phase"]
            if p == "before":
                return (step, 0, 0)
            elif p == "after":
                return (step, 9, 0)
            elif p.startswith("narrow"):
                try:
                    n = int(p.split()[1])
                except (IndexError, ValueError):
                    n = 0
                return (step, 1, n)
            elif p.startswith("overlay"):
                try:
                    n = int(p.split()[1])
                except (IndexError, ValueError):
                    n = 0
                return (step, 2, n)
            elif p.startswith("converge"):
                try:
                    n = int(p.split()[1])
                except (IndexError, ValueError):
                    n = 0
                return (step, 3, n)
            else:
                return (step, 5, 0)
        frame_list.sort(key=_phase_sort_key)

        # Build lookup: (step_num, overlay_round) -> frame index
        # and (step_num, "before") -> frame index, (step_num, "after") -> frame index
        frame_index_map = {}
        for fi, f in enumerate(frame_list):
            frame_index_map[(f["step"], f["phase"])] = fi

        # Build event feed — each event gets data-frame pointing to exact frame
        feed_html = ""
        _ACTION_LABEL = {
            "move_to": "gui:move", "click": "gui:click", "double_click": "gui:dblclick",
            "type_text": "gui:type", "key_press": "gui:key", "scroll": "gui:scroll",
            "drag": "gui:drag", "mouse_down": "gui:mousedown", "mouse_up": "gui:mouseup",
            "done": "done", "escalate": "escalate",
        }

        for i, step in enumerate(steps):
            step_num = step.get("step_num", "?")
            thought = step.get("thought", "")
            tool = step.get("tool", "")
            tool_args = step.get("tool_args", {})
            action = step.get("action", "")
            tool_result = step.get("tool_result", "")
            error = step.get("Error")
            internal = step.get("internal", [])

            args_str = ""
            if tool_args:
                args_str = ", ".join(f'{k}={json.dumps(v)[:60]}' for k, v in tool_args.items())

            label_text = _ACTION_LABEL.get(tool, f"gui:{tool}" if tool else "none")
            sender_cls = "sender-gui"
            if tool in ("done",): sender_cls = "sender-vision"
            elif tool in ("escalate",): sender_cls = "sender-wait"
            elif error: sender_cls = "sender-error"

            # Frame index for the "before" screenshot of this step
            before_fi = frame_index_map.get((step_num, "before"), 0)
            after_fi = frame_index_map.get((step_num, "after"), before_fi)

            # Step header
            feed_html += f'''<div class="lsv-step-header" data-frame="{before_fi}">Step {step_num}</div>'''

            # Track which overlay round we're on for linking grounding events
            overlay_round = 0

            # Render internal loop events with proper frame links
            for ev in internal:
                if ev["type"] == "supervisor" and ev.get("thought"):
                    # Supervisor thought — show with the overlay that follows (or before)
                    # Try to find the next grounding overlay
                    next_overlay_key = (step_num, f"overlay {overlay_round + 1}")
                    thought_fi = frame_index_map.get(next_overlay_key, before_fi)
                    feed_html += f'''<div class="lsv-event" data-frame="{thought_fi}">
                        <span class="lsv-sender sender-agent">think</span>
                        <span class="lsv-event-text">{_esc(ev["thought"])}</span></div>'''
                elif ev["type"] == "supervisor" and ev.get("action") == "move_to":
                    hint_str = f' hint="{_esc(ev["hint"])}"' if ev.get("hint") else ""
                    zoom_str = f' zoom={ev["zoom"]}' if ev.get("zoom") else ""
                    next_overlay_key = (step_num, f"overlay {overlay_round + 1}")
                    move_fi = frame_index_map.get(next_overlay_key, before_fi)
                    feed_html += f'''<div class="lsv-event" data-frame="{move_fi}">
                        <span class="lsv-sender sender-gui">gui:move</span>
                        <span class="lsv-event-text">target="{_esc(ev["target"])}"{hint_str}{zoom_str}</span></div>'''
                elif ev["type"] == "grounding":
                    overlay_round += 1
                    overlay_key = (step_num, f"overlay {overlay_round}")
                    grounding_fi = frame_index_map.get(overlay_key, before_fi)
                    if ev.get("error"):
                        feed_html += f'''<div class="lsv-event" data-frame="{grounding_fi}">
                            <span class="lsv-sender sender-error">ui-tars</span>
                            <span class="lsv-event-text">{_esc(ev["error"])}</span></div>'''
                    else:
                        edge = f' {_esc(ev["edge"])}' if ev.get("edge") else ""
                        feed_html += f'''<div class="lsv-event" data-frame="{grounding_fi}">
                            <span class="lsv-sender sender-vision">ui-tars</span>
                            <span class="lsv-event-text">({ev["x"]}, {ev["y"]}){edge}</span></div>'''
                elif ev["type"] == "redirect":
                    feed_html += f'''<div class="lsv-event" data-frame="{before_fi}">
                        <span class="lsv-sender sender-wait">redirect</span>
                        <span class="lsv-event-text">{_esc(ev["action"])}(target="{_esc(ev["target"])}") has no target param</span></div>'''

            # Final thought (if not already shown via internal events)
            if thought and not internal:
                feed_html += f'''<div class="lsv-event" data-frame="{before_fi}">
                    <span class="lsv-sender sender-agent">think</span>
                    <span class="lsv-event-text">{_esc(thought)}</span></div>'''

            # Final committed action — link to after frame
            feed_html += f'''<div class="lsv-event" data-frame="{after_fi}">
                <span class="lsv-sender {sender_cls}">{_esc(label_text)}</span>
                <span class="lsv-event-text">{_esc(args_str or action[:80])}</span></div>'''

            # Result
            if tool_result:
                feed_html += f'''<div class="lsv-event" data-frame="{after_fi}">
                    <span class="lsv-sender sender-tool">result</span>
                    <span class="lsv-event-text lsv-muted">{_esc(str(tool_result)[:200])}</span></div>'''

            # Error
            if error:
                feed_html += f'''<div class="lsv-event" data-frame="{after_fi}">
                    <span class="lsv-sender sender-error">error</span>
                    <span class="lsv-event-text">{_esc(error)}</span></div>'''

            # Step separator
            next_step = steps[i+1].get("step_num") if i+1 < len(steps) else None
            if next_step is not None and next_step != step_num:
                feed_html += '<div class="lsv-sep"></div>'

        frames_json = json.dumps(frame_list)

        html = f'''<!DOCTYPE html><html><head><meta charset="utf-8">
<title>{_esc(domain)} / {_esc(example_id[:12])}</title>
<style>{_LSV_CSS}
body {{ display: flex; flex-direction: column; }}
.lsv-step-header {{
  padding: 6px 12px; font-size: 10px; font-weight: 700; color: #38bdc1;
  text-transform: uppercase; letter-spacing: 0.6px; cursor: pointer;
  border-bottom: 1px solid var(--border); background: rgba(56,189,193,0.05);
}}
.lsv-step-header:hover {{ background: rgba(56,189,193,0.1); }}
.lsv-frame-desc {{
  font-size: 11px; color: var(--yellow); padding: 2px 12px 2px 0;
  text-align: left; font-weight: 600; min-width: 320px; max-width: 320px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}}
.lsv-scrubber-row .lsv-nav-btn {{ flex-shrink: 0; }}
.lsv-scrubber-row .lsv-frame-phase {{ min-width: 120px; }}
.lsv-scrubber-row .lsv-frame-label {{ min-width: 60px; }}
</style></head><body>

<div class="lsv-header">
  <div class="lsv-header-left">
    <a class="lsv-back" href="{url_prefix}/">&larr; tasks</a>
    <span class="lsv-title">osworld</span>
    <span class="lsv-session-id">{_esc(domain)}/{_esc(example_id[:12])}</span>
    <span class="lsv-score {score_cls}">{_esc(score_text)}</span>
  </div>
</div>

<div class="lsv-instruction-bar">
  <span class="lsv-instr-label">Instruction</span>
  <span class="lsv-instr-text">{_esc(instruction)}</span>
  <span class="lsv-instr-label" style="margin-left:12px">Steps</span>
  <span class="lsv-instr-text">{len(steps)}</span>
</div>

<div class="lsv-body">
  <div class="lsv-feed" id="feed">{feed_html}</div>
  <div class="lsv-viewer" id="viewer">
    <div class="lsv-frame-box">
      <img id="frame-img" src="" alt="screenshot">
    </div>
    <div class="lsv-scrubber-row">
      <button class="lsv-nav-btn" id="btn-prev">&#9664;</button>
      <input id="scrubber" class="lsv-scrubber" type="range" min="0" max="0" value="0">
      <button class="lsv-nav-btn" id="btn-next">&#9654;</button>
      <span class="lsv-frame-desc" id="frame-desc"></span>
      <span class="lsv-frame-phase" id="frame-phase"></span>
      <span class="lsv-frame-label" id="frame-label">0 / 0</span>
    </div>
  </div>
</div>

<script>
const FRAMES = {frames_json};
const img = document.getElementById('frame-img');
const scrubber = document.getElementById('scrubber');
const labelEl = document.getElementById('frame-label');
const phaseEl = document.getElementById('frame-phase');
const descEl = document.getElementById('frame-desc');
const feed = document.getElementById('feed');
const viewer = document.getElementById('viewer');
let currentFrame = -1;

// Preload all images
const preloaded = [];
FRAMES.forEach((f, i) => {{
  const p = new Image();
  p.src = f.src;
  preloaded[i] = p;
}});

if (FRAMES.length > 0) {{
  scrubber.max = FRAMES.length - 1;
  setFrame(0);
}}

function setFrame(n) {{
  n = Math.max(0, Math.min(n, FRAMES.length - 1));
  if (n === currentFrame) return;
  currentFrame = n;
  const f = FRAMES[n];
  if (!f) return;
  img.src = preloaded[n] ? preloaded[n].src : f.src;
  scrubber.value = n;
  labelEl.textContent = (n + 1) + ' / ' + FRAMES.length;
  phaseEl.textContent = 'step ' + f.step + ' ' + f.phase;
  descEl.textContent = f.label || '';

  // Highlight all feed events that point to this frame
  feed.querySelectorAll('[data-frame]').forEach(el => el.classList.remove('lsv-event-active'));
  feed.querySelectorAll('[data-frame="' + n + '"]').forEach(el => el.classList.add('lsv-event-active'));
  // Scroll first match into view
  const firstMatch = feed.querySelector('[data-frame="' + n + '"].lsv-event-active');
  if (firstMatch) {{
    firstMatch.scrollIntoView({{ block: 'nearest', behavior: 'smooth' }});
  }}
}}

scrubber.addEventListener('input', () => setFrame(parseInt(scrubber.value)));
document.getElementById('btn-prev').addEventListener('click', () => setFrame(currentFrame - 1));
document.getElementById('btn-next').addEventListener('click', () => setFrame(currentFrame + 1));

// Click event in feed -> snap to the frame this event is linked to
feed.querySelectorAll('[data-frame]').forEach(el => {{
  el.addEventListener('click', () => {{
    const fi = parseInt(el.dataset.frame);
    if (!isNaN(fi) && fi >= 0 && fi < FRAMES.length) {{
      currentFrame = -1;  // force update
      setFrame(fi);
    }}
  }});
}});

// Arrow keys
document.addEventListener('keydown', (e) => {{
  if (e.key === 'ArrowRight') {{ e.preventDefault(); setFrame(currentFrame + 1); }}
  if (e.key === 'ArrowLeft')  {{ e.preventDefault(); setFrame(currentFrame - 1); }}
}});

// Mouse wheel on viewer
viewer.addEventListener('wheel', (e) => {{
  e.preventDefault();
  if (e.deltaY > 0) setFrame(currentFrame + 1);
  else if (e.deltaY < 0) setFrame(currentFrame - 1);
}}, {{ passive: false }});
</script>
</body></html>'''
        self._respond(200, html)

    def _serve_image(self, rel_path, results_dir=None):
        base = results_dir or self.results_dir
        img_path = Path(base) / rel_path
        if not img_path.exists() or not img_path.is_file():
            self._404()
            return
        with open(img_path, "rb") as f:
            data = f.read()
        self.send_response(200)
        content_type = "image/jpeg" if img_path.suffix in (".jpg", ".jpeg") else "image/png"
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _respond(self, code, html):
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _404(self):
        self._respond(404, "<h1>404</h1>")

    def log_message(self, format, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description="OSWorld debug server")
    parser.add_argument("--port", type=int, default=8888)
    parser.add_argument("--results-dir", type=str, default=DEFAULT_RESULTS)
    args = parser.parse_args()

    class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True

    DebugHandler.results_dir = args.results_dir
    server = ThreadedHTTPServer(("0.0.0.0", args.port), DebugHandler)
    print(f"Debug server at http://localhost:{args.port}")
    print(f"Results dir: {args.results_dir}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
