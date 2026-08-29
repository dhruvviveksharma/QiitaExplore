"""Interactive harness to test the agentic chatbot WITHOUT the frontend/SSE.

Drives the real `stream_agent` loop and wraps `execute_tool` so you can see,
for any prompt:
  - which tools the LLM decides to call, with what args
  - how long each tool + each LLM round takes
  - the FULL text each tool returns to the LLM (the thing that decides quality)
  - the final assistant answer
  - reasoning-model thinking (minimax-m2, etc.) shown dimmed

Usage (run via run_agent_harness.sh so the Qiita/DB env is set):
  bash ../run_agent_harness.sh                             # interactive REPL (multi-turn)
  bash ../run_agent_harness.sh "studies on wild mice"      # one-shot prompt
  bash ../run_agent_harness.sh --model minimax-m2 "..."    # pick a model
  /deepsearch <prompt>                                     # enable deep search in REPL or one-shot
  bash ../run_agent_harness.sh --tool search_studies \\
       --args '{"keywords":["wild mice"]}'                 # tool only, no LLM
"""

import argparse
import json
import logging
import os
import re
import sys
import time

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


# ── Python-level tee: stdout → terminal (with colors) + log file (ANSI stripped) ──
_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')

class _LogTee:
    """Mirror all stdout to a log file with ANSI codes stripped."""
    def __init__(self, path, real_stdout):
        self._file    = open(path, "w", buffering=1, encoding="utf-8")
        self._out     = real_stdout
        self.encoding = real_stdout.encoding
        self.errors   = getattr(real_stdout, "errors", "replace")

    def write(self, s):
        self._out.write(s)
        self._file.write(_ANSI_RE.sub("", s))

    def flush(self):
        self._out.flush()
        self._file.flush()

    def isatty(self):
        return self._out.isatty()

    def fileno(self):
        return self._out.fileno()


# ── colors — check dynamically so they work after sys.stdout is replaced ──────
def _c(code, s): return f"\033[{code}m{s}\033[0m" if sys.stdout.isatty() else s
def dim(s):   return _c("2", s)
def bold(s):  return _c("1", s)
def cyan(s):  return _c("36", s)
def green(s): return _c("32", s)
def yellow(s):return _c("33", s)

TEXT_PREVIEW = int(os.getenv("HARNESS_TEXT_PREVIEW", "2000"))  # chars of tool text to show
CHAT_ID = "harness-test-chat"

_TRACE = []  # per-prompt list of {name, args, dt, label, detail, text_len}


def _traced_execute_tool(name, args, **kw):
    """Wrap the real execute_tool to print full I/O and record timing."""
    print(cyan(f"\n  ┌─ TOOL  {name}({json.dumps(args, ensure_ascii=False)})"))
    t0 = time.perf_counter()
    result = execute_tool(name, args, **kw)
    dt = time.perf_counter() - t0
    print(cyan(f"  ├─ {dt:.2f}s  ") + f"{result.label} · {result.detail}")
    text = result.text or ""
    preview = text[:TEXT_PREVIEW] + (dim(f"  …(+{len(text)-TEXT_PREVIEW} chars)") if len(text) > TEXT_PREVIEW else "")
    print(dim("  │  RETURNED TO LLM:"))
    for line in preview.splitlines():
        print(dim(f"  │  {line}"))
    print(cyan("  └─────"))
    _TRACE.append({"name": name, "args": args, "dt": dt,
                   "label": result.label, "detail": result.detail, "text_len": len(text)})
    return result


def run_prompt(prompt, model, history, deep_search=False):
    """Run one turn through the real agent loop. Returns the assistant text."""
    _TRACE.clear()
    messages = list(history) + [{"role": "user", "content": prompt}]
    mode = "DEEP" if deep_search else "normal"
    print(bold(f"\nYOU: {prompt}"))
    print(dim(f"(model={model}  scope={SCOPE_GLOBAL}  mode={mode})"))

    assistant_parts = []
    t_total = time.perf_counter()
    printed_assistant_header  = False
    printed_reasoning_header  = False

    for event in stream_agent(
        messages,
        system_prompt=GLOBAL_CHAT_SYSTEM_PROMPT,
        model=model,
        study_context_text=None,
        scope=SCOPE_GLOBAL,
        chat_id=CHAT_ID,
        deep_search=deep_search,
        max_iters=10,
        tools=TOOL_SCHEMAS,
    ):
        etype = event["type"]

        if etype == "reasoning":
            if not printed_reasoning_header:
                print(dim("\n[thinking] "), end="")
                printed_reasoning_header = True
            sys.stdout.write(dim(event["token"]))
            sys.stdout.flush()

        elif etype == "token":
            if printed_reasoning_header:
                # Blank line after reasoning block before the real answer
                print()
                printed_reasoning_header = False
            if not printed_assistant_header:
                print(green("\nASSISTANT: "), end="")
                printed_assistant_header = True
            assistant_parts.append(event["token"])
            sys.stdout.write(event["token"])
            sys.stdout.flush()

        elif etype == "segment_tool_call":
            if printed_reasoning_header:
                print()
                printed_reasoning_header = False
            print(yellow(f"\n  → LLM requested: {event['label']}"))
        # segment_tool_result is already covered by _traced_execute_tool print

    total = time.perf_counter() - t_total
    print(bold("\n\n── summary ───────────────────────────────────────"))
    print(f"  total wall time : {total:.2f}s")
    if _TRACE:
        print(f"  tool calls      : {len(_TRACE)}")
        for t in _TRACE:
            print(f"    • {t['name']:<18} {t['dt']:>6.2f}s   {t['detail']}  ({t['text_len']} chars)")
        slowest = max(_TRACE, key=lambda t: t["dt"])
        print(yellow(f"  bottleneck      : {slowest['name']} ({slowest['dt']:.2f}s)"))
    else:
        print("  tool calls      : none (LLM answered directly)")
    print("──────────────────────────────────────────────────")
    return "".join(assistant_parts)


def run_tool(name, args):
    """Call a single tool directly — no LLM. Deterministic tool-output check."""
    print(bold(f"\nDIRECT TOOL CALL: {name}({json.dumps(args)})"))
    _traced_execute_tool(name, args, scope=SCOPE_GLOBAL, chat_id=CHAT_ID)


def repl(model, deep_search=False):
    mode = "DEEP" if deep_search else "normal"
    print(bold(f"Agent harness — interactive ({mode} mode). Type a prompt; 'quit' to exit.\n"
               "Prefix prompt with /deepsearch to enable deep search for that turn."))
    history = []
    while True:
        try:
            prompt = input(bold("\nyou> ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if prompt.lower() in {"quit", "exit", "q"}:
            break
        if not prompt:
            continue
        turn_deep = deep_search
        if prompt.startswith("/deepsearch "):
            prompt    = prompt[len("/deepsearch "):].strip()
            turn_deep = True
        answer = run_prompt(prompt, model, history, deep_search=turn_deep)
        history.append({"role": "user",      "content": prompt})
        history.append({"role": "assistant", "content": answer})


def main():
    # ── set up log tee FIRST so all subsequent output (incl. logging) goes to file ──
    log_fp = os.getenv("HARNESS_LOG_FP")
    if not log_fp:
        logs_dir = os.path.join(_BACKEND_DIR, "..", "logs")
        os.makedirs(logs_dir, exist_ok=True)
        ts     = time.strftime("%Y%m%d_%H%M%S")
        log_fp = os.path.join(logs_dir, f"harness_{ts}.log")
    sys.stdout = _LogTee(log_fp, sys.__stdout__)
    print(f"[log → {log_fp}]")

    # ── configure logging to go through the tee ────────────────────────────────
    level = logging.DEBUG if os.getenv("AGENT_DEBUG") else logging.INFO
    logging.basicConfig(
        level=level,
        stream=sys.stdout,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # Suppress very noisy third-party loggers that don't help with app debugging
    for _noisy in ("httpcore", "httpx", "openai._base_client", "urllib3",
                   "h5py", "matplotlib", "PIL"):
        logging.getLogger(_noisy).setLevel(logging.WARNING)

    # ── lazy-import agent modules after logging/path setup ─────────────────────
    global stream_agent, execute_tool, GLOBAL_CHAT_SYSTEM_PROMPT, SCOPE_GLOBAL, TOOL_SCHEMAS, agent_mod
    import helpers.agent as agent_mod
    from helpers.agent import stream_agent
    from helpers.agent_tool_schemas import TOOL_SCHEMAS
    from helpers.agent_tools import execute_tool
    from config import GLOBAL_CHAT_SYSTEM_PROMPT
    from store.cache import SCOPE_GLOBAL

    # Patch execute_tool inside agent module so stream_agent uses the traced version
    agent_mod.execute_tool = _traced_execute_tool

    ap = argparse.ArgumentParser(description="Test the agentic chatbot from the CLI.")
    ap.add_argument("prompt", nargs="?", help="one-shot prompt; omit for interactive REPL")
    ap.add_argument("--model", default="minimax-m2", help="model id (default minimax-m2; tool-capable)")
    ap.add_argument("--tool",  help="call one tool directly (no LLM)")
    ap.add_argument("--args",  default="{}", help="JSON args for --tool")
    ap.add_argument("--deep",  action="store_true",
                    help="enable deep search (sample metadata scan across ~500 studies)")
    a = ap.parse_args()

    if a.tool:
        run_tool(a.tool, json.loads(a.args))
    elif a.prompt:
        prompt    = a.prompt
        turn_deep = a.deep
        if prompt.startswith("/deepsearch "):
            prompt    = prompt[len("/deepsearch "):].strip()
            turn_deep = True
        run_prompt(prompt, a.model, [], deep_search=turn_deep)
    else:
        repl(a.model, deep_search=a.deep)


if __name__ == "__main__":
    main()
