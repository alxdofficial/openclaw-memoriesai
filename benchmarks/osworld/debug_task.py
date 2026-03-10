"""Debug viewer for OSWorld task results.

Prints a structured step-by-step report with thoughts, actions, tool calls,
and screenshot file paths. Designed to be read by an AI agent for debugging.

Usage:
    python3 benchmarks/osworld/debug_task.py <result_dir>
    python3 benchmarks/osworld/debug_task.py results/pyautogui/screenshot/detm-gemini-uitars/chrome/bb5e4c0d-...

    # Show all failed tasks:
    python3 benchmarks/osworld/debug_task.py --failed [results_base_dir]

    # Show summary of all tasks:
    python3 benchmarks/osworld/debug_task.py --summary [results_base_dir]
"""

import json
import os
import sys
from pathlib import Path


OSWORLD_DIR = os.environ.get("OSWORLD_DIR", "/home/alex/OSWorld")
DEFAULT_RESULTS = os.path.join(OSWORLD_DIR, "results/pyautogui/screenshot/detm-gemini-uitars")


def load_task_config(domain, example_id):
    """Load the original task config to get the instruction."""
    config_file = os.path.join(OSWORLD_DIR, f"evaluation_examples/examples/{domain}/{example_id}.json")
    if os.path.exists(config_file):
        with open(config_file) as f:
            return json.load(f)
    return None


def debug_task(result_dir):
    """Print structured debug report for one task."""
    result_dir = Path(result_dir)
    traj_file = result_dir / "traj.jsonl"
    result_file = result_dir / "result.txt"

    if not traj_file.exists():
        print(f"No trajectory found at {traj_file}")
        return

    # Extract domain/example_id from path
    example_id = result_dir.name
    domain = result_dir.parent.name

    # Load task config
    config = load_task_config(domain, example_id)
    instruction = config["instruction"] if config else "UNKNOWN"

    # Load result
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
    screenshot_map = {}  # step_num -> {"before": path, "after": path}
    for s in screenshots:
        name = s.stem  # e.g. "step_1_before_20260308..." or "step_1_20260308..."
        parts = name.split("_")
        step_num = int(parts[1])
        if step_num not in screenshot_map:
            screenshot_map[step_num] = {}
        if "before" in name:
            screenshot_map[step_num]["before"] = str(s)
        elif "after" in name:
            screenshot_map[step_num]["after"] = str(s)
        else:
            # Old format without before/after
            screenshot_map[step_num]["after"] = str(s)

    # Print report
    print("=" * 80)
    print(f"TASK DEBUG REPORT")
    print(f"=" * 80)
    print(f"Domain:      {domain}")
    print(f"Example ID:  {example_id}")
    print(f"Instruction: {instruction}")
    print(f"Score:       {score}")
    print(f"Steps:       {len(steps)}")
    print(f"Result dir:  {result_dir}")
    print()

    for step in steps:
        step_num = step.get("step_num", "?")
        print(f"--- Step {step_num} ---")

        # Screenshot the agent saw
        ss = screenshot_map.get(step_num, {})
        if ss.get("before"):
            print(f"  Screenshot (before): {ss['before']}")

        # Agent's reasoning
        thought = step.get("thought", "")
        if thought:
            print(f"  Thought: {thought}")

        # Tool call
        tool = step.get("tool")
        if tool:
            args = step.get("tool_args", {})
            args_str = ", ".join(f'{k}={json.dumps(v)}' for k, v in args.items())
            print(f"  Tool call: {tool}({args_str})")

        # Resulting pyautogui action
        action = step.get("action", "")
        print(f"  Action:   {action}")

        # Tool result
        result = step.get("tool_result", "")
        if result:
            print(f"  Result:   {result}")

        # Reward
        reward = step.get("reward")
        if reward is not None:
            print(f"  Reward:   {reward}")

        # Screenshot after action
        if ss.get("after"):
            print(f"  Screenshot (after):  {ss['after']}")

        # Error
        error = step.get("Error")
        if error:
            print(f"  ERROR: {error}")

        print()

    print("=" * 80)


def summary(results_base):
    """Print summary of all tasks."""
    results_base = Path(results_base)
    if not results_base.exists():
        print(f"Results dir not found: {results_base}")
        return

    tasks = []
    for domain_dir in sorted(results_base.iterdir()):
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

            config = load_task_config(domain, example_id)
            instruction = config["instruction"][:80] if config else "?"

            tasks.append({
                "domain": domain,
                "id": example_id,
                "score": score,
                "steps": n_steps,
                "instruction": instruction,
                "dir": str(example_dir),
            })

    if not tasks:
        print("No tasks found.")
        return

    succeeded = sum(1 for t in tasks if t["score"] and t["score"] > 0)
    total = len(tasks)

    print(f"{'Domain':<25} {'Score':>5} {'Steps':>5}  Instruction")
    print("-" * 100)
    for t in tasks:
        score_str = f"{t['score']:.1f}" if t["score"] is not None else "N/A"
        marker = "PASS" if t["score"] and t["score"] > 0 else "FAIL"
        print(f"{t['domain']:<25} {score_str:>5} {t['steps']:>5}  [{marker}] {t['instruction']}")

    print("-" * 100)
    print(f"Total: {total}  Passed: {succeeded}  Score: {succeeded/total*100:.1f}%")


def show_failed(results_base):
    """Show detailed debug for all failed tasks."""
    results_base = Path(results_base)
    for domain_dir in sorted(results_base.iterdir()):
        if not domain_dir.is_dir():
            continue
        for example_dir in sorted(domain_dir.iterdir()):
            if not example_dir.is_dir():
                continue
            result_file = example_dir / "result.txt"
            if result_file.exists():
                try:
                    score = float(result_file.read_text().strip())
                    if score > 0:
                        continue
                except ValueError:
                    pass
            debug_task(str(example_dir))
            print("\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    if sys.argv[1] == "--summary":
        base = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_RESULTS
        summary(base)
    elif sys.argv[1] == "--failed":
        base = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_RESULTS
        show_failed(base)
    else:
        debug_task(sys.argv[1])
