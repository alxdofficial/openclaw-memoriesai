"""Generate self-contained HTML debug reports for OSWorld task runs.

Reads trajectory + screenshots from a result directory and produces
a single HTML file with an interactive step-by-step viewer.

Usage:
    python3 benchmarks/osworld/generate_debug_html.py <result_dir>
    python3 benchmarks/osworld/generate_debug_html.py --all [results_base_dir]
"""

import base64
import json
import os
import sys
from pathlib import Path

OSWORLD_DIR = os.environ.get("OSWORLD_DIR", "/home/alex/OSWorld")
DEFAULT_RESULTS = os.path.join(OSWORLD_DIR, "results/pyautogui/screenshot/detm-gemini-uitars")


def load_task_config(domain, example_id):
    config_file = os.path.join(OSWORLD_DIR, f"evaluation_examples/examples/{domain}/{example_id}.json")
    if os.path.exists(config_file):
        with open(config_file) as f:
            return json.load(f)
    return None


def img_to_data_uri(path):
    """Convert image file to base64 data URI."""
    if not os.path.exists(path):
        return ""
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    ext = Path(path).suffix.lower()
    mime = "image/png" if ext == ".png" else "image/jpeg"
    return f"data:{mime};base64,{data}"


def generate_html(result_dir):
    """Generate self-contained HTML debug report for one task."""
    result_dir = Path(result_dir)
    traj_file = result_dir / "traj.jsonl"
    result_file = result_dir / "result.txt"

    if not traj_file.exists():
        print(f"No trajectory at {traj_file}")
        return None

    example_id = result_dir.name
    domain = result_dir.parent.name
    config = load_task_config(domain, example_id)
    instruction = config["instruction"] if config else "UNKNOWN"

    score = "N/A"
    if result_file.exists():
        score = result_file.read_text().strip()

    # Load trajectory
    steps = []
    with open(traj_file) as f:
        for line in f:
            line = line.strip()
            if line:
                steps.append(json.loads(line))

    # Find screenshots
    screenshots = sorted(result_dir.glob("step_*.png"))
    screenshot_map = {}
    for s in screenshots:
        name = s.stem
        parts = name.split("_")
        step_num = int(parts[1])
        if step_num not in screenshot_map:
            screenshot_map[step_num] = {}
        if "before" in name:
            screenshot_map[step_num]["before"] = str(s)
        elif "after" in name:
            screenshot_map[step_num]["after"] = str(s)
        else:
            screenshot_map[step_num]["after"] = str(s)

    # Load internal debug frames if saved
    internal_dir = result_dir / "internal_frames"
    internal_frames = {}
    if internal_dir.exists():
        for f in sorted(internal_dir.glob("*.png")):
            # Format: step_N_round_M.png
            parts = f.stem.split("_")
            step_num = int(parts[1])
            round_num = int(parts[3])
            if step_num not in internal_frames:
                internal_frames[step_num] = []
            internal_frames[step_num].append({"round": round_num, "path": str(f)})

    # Build HTML
    score_class = "score-pass" if score.strip() not in ("0", "0.0", "N/A") else "score-fail"

    events_html = ""
    for step in steps:
        step_num = step.get("step_num", "?")
        ss = screenshot_map.get(step_num, {})
        thought = step.get("thought", "")
        tool = step.get("tool", "")
        tool_args = step.get("tool_args", {})
        action = step.get("action", "")
        tool_result = step.get("tool_result", "")
        error = step.get("Error")

        # Format tool args
        args_str = ""
        if tool_args:
            args_str = ", ".join(f'{k}={json.dumps(v)[:80]}' for k, v in tool_args.items())

        # Before screenshot
        before_img = ""
        if ss.get("before"):
            before_uri = img_to_data_uri(ss["before"])
            before_img = f'<img class="screenshot" src="{before_uri}" alt="before">'

        # After screenshot
        after_img = ""
        if ss.get("after"):
            after_uri = img_to_data_uri(ss["after"])
            after_img = f'<img class="screenshot" src="{after_uri}" alt="after">'

        # Internal refinement frames
        internal_html = ""
        if step_num in internal_frames:
            for frame_info in internal_frames[step_num]:
                frame_uri = img_to_data_uri(frame_info["path"])
                internal_html += f'''
                    <div class="internal-frame">
                        <span class="internal-label">refinement round {frame_info["round"]}</span>
                        <img class="screenshot" src="{frame_uri}" alt="refinement">
                    </div>
                '''

        # Build the event block
        events_html += f'''
        <div class="step" id="step-{step_num}">
            <div class="step-header" onclick="toggleStep(this)">
                <span class="step-num">Step {step_num}</span>
                <span class="step-tool {'tool-error' if error else ''}">{_esc(tool or 'none')}({_esc(args_str[:60])})</span>
                <span class="step-action">{_esc(action[:80])}</span>
            </div>
            <div class="step-body">
                <div class="step-columns">
                    <div class="step-feed">
                        {f'<div class="event event-thought"><span class="label label-thought">thought</span><span class="text">{_esc(thought)}</span></div>' if thought else ''}
                        <div class="event event-tool"><span class="label label-tool">{_esc(tool or "none")}</span><span class="text">{_esc(args_str)}</span></div>
                        <div class="event event-action"><span class="label label-action">action</span><span class="text mono">{_esc(action)}</span></div>
                        {f'<div class="event event-result"><span class="label label-result">result</span><span class="text">{_esc(tool_result)}</span></div>' if tool_result else ''}
                        {f'<div class="event event-error"><span class="label label-error">ERROR</span><span class="text">{_esc(error)}</span></div>' if error else ''}
                        {internal_html}
                    </div>
                    <div class="step-screenshots">
                        {f'<div class="ss-group"><span class="ss-label">before</span>{before_img}</div>' if before_img else ''}
                        {f'<div class="ss-group"><span class="ss-label">after</span>{after_img}</div>' if after_img else ''}
                    </div>
                </div>
            </div>
        </div>
        '''

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{_esc(domain)} / {_esc(example_id[:12])} — OSWorld Debug</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace; background: #0d1117; color: #c9d1d9; }}

.header {{
    background: #161b22; border-bottom: 1px solid #30363d; padding: 16px 24px;
    display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
}}
.header-title {{ font-size: 16px; font-weight: 600; color: #f0f6fc; }}
.header-domain {{ color: #8b949e; font-size: 13px; }}
.header-score {{ font-size: 14px; font-weight: 600; padding: 2px 10px; border-radius: 12px; }}
.score-pass {{ background: #1b4332; color: #40c057; }}
.score-fail {{ background: #3d1f1f; color: #f85149; }}
.instruction {{ background: #1c2128; padding: 12px 24px; border-bottom: 1px solid #30363d; font-size: 13px; }}
.instruction .label {{ color: #8b949e; margin-right: 8px; }}
.instruction .text {{ color: #e6edf3; }}

.steps {{ padding: 8px; }}

.step {{
    background: #161b22; border: 1px solid #30363d; border-radius: 8px;
    margin: 6px 0; overflow: hidden;
}}
.step-header {{
    display: flex; align-items: center; gap: 12px; padding: 10px 16px;
    cursor: pointer; user-select: none;
}}
.step-header:hover {{ background: #1c2128; }}
.step-num {{ color: #58a6ff; font-weight: 600; font-size: 13px; min-width: 55px; }}
.step-tool {{ color: #d2a8ff; font-size: 12px; min-width: 200px; font-family: monospace; }}
.step-action {{ color: #8b949e; font-size: 12px; font-family: monospace; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.tool-error {{ color: #f85149; }}

.step-body {{ display: none; border-top: 1px solid #30363d; }}
.step.open .step-body {{ display: block; }}

.step-columns {{ display: flex; gap: 0; }}
.step-feed {{ flex: 1; padding: 12px 16px; min-width: 0; border-right: 1px solid #30363d; }}
.step-screenshots {{ flex: 0 0 auto; padding: 12px; display: flex; flex-direction: column; gap: 8px; max-width: 50%; }}

.event {{ display: flex; align-items: flex-start; gap: 8px; margin: 4px 0; font-size: 13px; }}
.label {{
    flex: 0 0 auto; padding: 1px 8px; border-radius: 4px; font-size: 11px;
    font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;
}}
.label-thought {{ background: #1f3a5f; color: #79c0ff; }}
.label-tool {{ background: #2d1f4e; color: #d2a8ff; }}
.label-action {{ background: #1f3a2e; color: #7ee787; }}
.label-result {{ background: #2d2a1f; color: #e3b341; }}
.label-error {{ background: #3d1f1f; color: #f85149; }}
.text {{ word-break: break-word; line-height: 1.5; }}
.mono {{ font-family: monospace; }}

.ss-group {{ text-align: center; }}
.ss-label {{ display: block; color: #8b949e; font-size: 11px; margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.5px; }}
.screenshot {{ max-width: 480px; max-height: 300px; border-radius: 4px; border: 1px solid #30363d; cursor: pointer; transition: max-width 0.2s; }}
.screenshot:hover {{ max-width: 720px; max-height: 450px; }}
.screenshot.expanded {{ max-width: 960px; max-height: 600px; position: relative; z-index: 10; }}

.internal-frame {{ margin: 6px 0; padding: 4px 0 4px 16px; border-left: 2px solid #30363d; }}
.internal-label {{ color: #8b949e; font-size: 11px; display: block; margin-bottom: 4px; }}
.internal-frame .screenshot {{ max-width: 320px; max-height: 200px; }}

.nav {{ display: flex; gap: 4px; padding: 8px 24px; background: #161b22; border-bottom: 1px solid #30363d; flex-wrap: wrap; }}
.nav-btn {{
    background: #21262d; color: #c9d1d9; border: 1px solid #30363d; padding: 4px 12px;
    border-radius: 6px; cursor: pointer; font-size: 12px; font-family: monospace;
}}
.nav-btn:hover {{ background: #30363d; }}
.nav-btn.active {{ background: #1f6feb; color: #fff; border-color: #1f6feb; }}
</style>
</head>
<body>

<div class="header">
    <span class="header-title">OSWorld Debug</span>
    <span class="header-domain">{_esc(domain)} / {_esc(example_id[:16])}</span>
    <span class="header-score {score_class}">Score: {_esc(score)}</span>
    <span class="header-domain">{len(steps)} steps</span>
</div>

<div class="instruction">
    <span class="label">Instruction</span>
    <span class="text">{_esc(instruction)}</span>
</div>

<div class="nav" id="step-nav"></div>

<div class="steps" id="steps">
{events_html}
</div>

<script>
function toggleStep(header) {{
    const step = header.parentElement;
    const wasOpen = step.classList.contains('open');
    // Close all
    document.querySelectorAll('.step').forEach(s => s.classList.remove('open'));
    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
    // Toggle
    if (!wasOpen) {{
        step.classList.add('open');
        const id = step.id;
        const btn = document.querySelector(`.nav-btn[data-step="${{id}}"]`);
        if (btn) btn.classList.add('active');
    }}
}}

function goToStep(stepId) {{
    const step = document.getElementById(stepId);
    if (!step) return;
    document.querySelectorAll('.step').forEach(s => s.classList.remove('open'));
    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
    step.classList.add('open');
    step.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
    const btn = document.querySelector(`.nav-btn[data-step="${{stepId}}"]`);
    if (btn) btn.classList.add('active');
}}

// Screenshots: click to expand
document.querySelectorAll('.screenshot').forEach(img => {{
    img.addEventListener('click', () => img.classList.toggle('expanded'));
}});

// Build step nav
const nav = document.getElementById('step-nav');
document.querySelectorAll('.step').forEach(step => {{
    const btn = document.createElement('button');
    btn.className = 'nav-btn';
    btn.dataset.step = step.id;
    btn.textContent = step.id.replace('step-', 'S');
    btn.addEventListener('click', () => goToStep(step.id));
    nav.appendChild(btn);
}});

// Open first step
const first = document.querySelector('.step');
if (first) {{ first.classList.add('open'); const btn = document.querySelector('.nav-btn'); if (btn) btn.classList.add('active'); }}

// Arrow key navigation
document.addEventListener('keydown', (e) => {{
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {{
        e.preventDefault();
        const current = document.querySelector('.step.open');
        const next = current ? current.nextElementSibling : document.querySelector('.step');
        if (next && next.classList.contains('step')) goToStep(next.id);
    }}
    if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {{
        e.preventDefault();
        const current = document.querySelector('.step.open');
        const prev = current ? current.previousElementSibling : null;
        if (prev && prev.classList.contains('step')) goToStep(prev.id);
    }}
}});
</script>
</body>
</html>'''

    # Write HTML
    out_path = result_dir / "debug.html"
    with open(out_path, "w") as f:
        f.write(html)
    print(f"Generated: {out_path}")
    return str(out_path)


def _esc(s):
    """HTML-escape a string."""
    if s is None:
        return ""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def generate_all(results_base):
    """Generate HTML for all tasks."""
    results_base = Path(results_base)
    for domain_dir in sorted(results_base.iterdir()):
        if not domain_dir.is_dir():
            continue
        for example_dir in sorted(domain_dir.iterdir()):
            if not example_dir.is_dir():
                continue
            if (example_dir / "traj.jsonl").exists():
                generate_html(str(example_dir))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    if sys.argv[1] == "--all":
        base = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_RESULTS
        generate_all(base)
    else:
        generate_html(sys.argv[1])
