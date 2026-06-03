"""Interactive harness to test the agentic chatbot WITHOUT the frontend/SSE.

Drives the real `stream_agent` loop and wraps `execute_tool` so you can see,
for any prompt:
  - which tools the LLM decides to call, with what args
  - how long each tool + each LLM round takes
  - the FULL text each tool returns to the LLM (the thing that decides quality)
  - the final assistant answer

Usage (run via run_agent_harness.sh so the Qiita/DB env is set):
  bash ../run_agent_harness.sh                          # interactive REPL (multi-turn)
  bash ../run_agent_harness.sh "studies on wild mice"   # one-shot prompt
  bash ../run_agent_harness.sh --model qwen3 "..."       # pick a model
  bash ../run_agent_harness.sh --tool search_studies \
       --args '{"keywords":["wild mice"],"data_types":["Metagenomic"]}'   # tool only, no LLM
"""

import argparse
import json
import os
import sys
import time

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

import helpers.agent as agent_mod            # noqa: E402
from helpers.agent import stream_agent       # noqa: E402
from helpers.agent_tools import execute_tool  # noqa: E402
from config import GLOBAL_CHAT_SYSTEM_PROMPT, DEFAULT_MODEL  # noqa: E402
from store.cache import SCOPE_GLOBAL          # noqa: E402

TEXT_PREVIEW = int(os.getenv("HARNESS_TEXT_PREVIEW", "2000"))  # chars of tool text to show
CHAT_ID = "harness-test-chat"

# ── colors (no-op if not a tty) ──────────────────────────────────────────────
_C = sys.stdout.isatty()
def _c(code, s): return f"\033[{code}m{s}\033[0m" if _C else s
def dim(s):  return _c("2", s)
def bold(s): return _c("1", s)
def cyan(s): return _c("36", s)
def green(s):return _c("32", s)
def yellow(s):return _c("33", s)

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


# Patch the name bound INSIDE helpers.agent (stream_agent calls it directly)
agent_mod.execute_tool = _traced_execute_tool


def run_prompt(prompt, model, history, deep_search=False):
    """Run one turn through the real agent loop. Returns the assistant text."""
    _TRACE.clear()
    messages = list(history) + [{"role": "user", "content": prompt}]
    mode = "DEEP" if deep_search else "normal"
    print(bold(f"\nYOU: {prompt}"))
    print(dim(f"(model={model}  scope={SCOPE_GLOBAL}  mode={mode})"))

    assistant_parts = []
    t_total = time.perf_counter()
    printed_assistant_header = False

    for event in stream_agent(
        messages,
        system_prompt=GLOBAL_CHAT_SYSTEM_PROMPT,
        model=model,
        study_context_text=None,
        scope=SCOPE_GLOBAL,
        chat_id=CHAT_ID,
        deep_search=deep_search,
    ):
        etype = event["type"]
        if etype == "token":
            if not printed_assistant_header:
                print(green("\nASSISTANT: "), end="")
                printed_assistant_header = True
            assistant_parts.append(event["token"])
            sys.stdout.write(event["token"])
            sys.stdout.flush()
        elif etype == "segment_tool_call":
            print(yellow(f"\n  → LLM requested: {event['label']}"))
        # segment_tool_result is already covered by the _traced_execute_tool print

    total = time.perf_counter() - t_total
    print(bold(f"\n\n── summary ───────────────────────────────────────"))
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
               "Conversation history is kept so you can test multi-turn (e.g. search then filter)."))
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
        answer = run_prompt(prompt, model, history, deep_search=deep_search)
        history.append({"role": "user", "content": prompt})
        history.append({"role": "assistant", "content": answer})


def main():
    ap = argparse.ArgumentParser(description="Test the agentic chatbot from the CLI.")
    ap.add_argument("prompt", nargs="?", help="one-shot prompt; omit for interactive REPL")
    ap.add_argument("--model", default="qwen3", help="model id (default qwen3; tool-capable)")
    ap.add_argument("--tool", help="call one tool directly (no LLM)")
    ap.add_argument("--args", default="{}", help="JSON args for --tool")
    ap.add_argument("--deep", action="store_true", help="enable deep search (sample metadata scan across ~500 studies)")
    a = ap.parse_args()

    if a.tool:
        run_tool(a.tool, json.loads(a.args))
    elif a.prompt:
        run_prompt(a.prompt, a.model, [], deep_search=a.deep)
    else:
        repl(a.model, deep_search=a.deep)


if __name__ == "__main__":
    main()
