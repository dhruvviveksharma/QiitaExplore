"""Slash-command steering for the agent loop (helpers/agent.py).

A study slash command (frontend js/chat_slash.js, e.g. "/graph 10317 1115")
sends force_tool {name, args, text}: the turn's first round(s) must call that
tool before the model says anything. Each forced round:
  - offers only the forced tool's schema;
  - Anthropic: names it in tool_choice (the API guarantees the call);
  - OpenAI-compatible (NRP): sends tool_choice only where MODEL_METADATA's
    `forced_tool_choice` says the server honours it ("required" / "named");
  - hides the model's text (the widget is the answer; it comments after);
and the user turn carries a live-only hint naming the tool. If the model still
doesn't call it, the loop calls it anyway with the forced args, so a slash
command always shows its widget (logged as force_tool_synthesized).

Without a study id ("/preps the AGP preps") the plan is resolve_study first:
if that settles the study, the target tool for it; if not, one round with no
tools, in which the model asks the user to pick from the candidates shown.
"""
import json
import uuid

RESOLVE = "resolve_study"


class ForcedPlan:
    def __init__(self, force):
        self.target = force["name"]
        self.args = dict(force.get("args") or {})       # forced keys win over the model's
        self.text = force.get("text") or ""
        self.queue = [self.target] if "study_id" in self.args else [RESOLVE, self.target]
        self.ask_next = False                           # resolve was ambiguous

    def current(self):
        """The tool this round must call, or None."""
        return self.queue[0] if self.queue else None

    def forced_args(self, name):
        return {"text": self.text, "for_tool": self.target} if name == RESOLVE else dict(self.args)

    def tools(self, tools, name_of):
        """Tools to offer this round, from `tools` (provider-shaped; name_of
        reads a tool's name): the forced one alone, none for the ask round,
        else `tools` unchanged."""
        name = self.current()
        if name:
            return [t for t in tools if name_of(t) == name]
        if self.ask_next:
            self.ask_next = False
            return []
        return tools

    def pick(self, calls):
        """(call_id, name, args, synthesized) for this forced round from the
        model's calls [(id, name, args)]: its first call to the forced tool with
        the forced args merged in, else a synthetic call with the forced args."""
        name = self.current()
        for cid, cname, cargs in calls:
            if cname == name:
                return cid, name, {**(cargs if isinstance(cargs, dict) else {}), **self.forced_args(name)}, False
        return f"force_{uuid.uuid4().hex[:12]}", name, self.forced_args(name), True

    def done(self, name, ui_payload):
        """Advance after a call to `name` finished with `ui_payload`."""
        if not self.queue or name != self.queue[0]:
            return
        self.queue.pop(0)
        if name == RESOLVE:
            if (ui_payload or {}).get("kind") == "study_resolved":
                self.args["study_id"] = ui_payload["study_id"]
            else:                                       # ambiguous or not found: the user picks
                self.queue = []
                self.ask_next = True

    def hint(self):
        if self.current() == RESOLVE:
            return (f"[Slash command for {self.target}, without a study id. First call resolve_study with the "
                    f"user's words. If it resolves, call {self.target} for that study; if it doesn't, ask the "
                    "user in one sentence to pick from the candidates shown.]")
        return (f"[Slash command: call {self.target} with {json.dumps(self.args)} first, then comment in "
                "2–4 sentences on what it shows.]")


def add_hint(api_msgs, hint):
    """Append the hint to the live user turn (never persisted)."""
    last = api_msgs[-1] if api_msgs else None
    if last and last.get("role") == "user" and isinstance(last.get("content"), str):
        api_msgs[-1] = {**last, "content": f"{last['content']}\n\n{hint}"}


def openai_tool_choice(name, flag):
    """tool_choice for an OpenAI-compatible server, per the model's
    MODEL_METADATA forced_tool_choice flag; None = don't send one."""
    if flag == "named":
        return {"type": "function", "function": {"name": name}}
    if flag == "required":
        return "required"
    return None


def anthropic_tool_choice(name):
    return {"type": "tool", "name": name, "disable_parallel_tool_use": True}
