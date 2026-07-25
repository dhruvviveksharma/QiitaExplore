"""Internal HTTP surface the pi sidecar calls to execute qiita tools.

Two endpoints. This is a genuine CROSS-MACHINE boundary — the sidecar runs on
the intermediate node, Flask on barnacle — so nothing here may assume a
loopback caller. Both endpoints pass through _guard(): source-IP allowlist
plus the X-Pi-Secret shared secret. The tool-call endpoint then additionally
requires a signed per-turn scope token.

  GET  /api/internal/tools/schemas   — _guard() only. Returns
                                        agent_tools.TOOL_SCHEMAS verbatim, so
                                        the sidecar's tool set can never drift
                                        from qiita_explore's.
  POST /api/internal/tools/<name>    — _guard() + X-Tool-Token scope token
                                        (never the session cookie). Dispatches
                                        to execute_tool(), enforcing a HARD
                                        project-scope boundary when
                                        scope == "project" — this is what
                                        upgrades project chat from a prompt
                                        request to a real one.

Three independent gates, so no single leaked value is sufficient: the secret
alone can't be replayed from an unlisted host, and a captured scope token
alone can't be used without the secret.
"""

import hmac
import logging

from flask import jsonify, request

from run import app
import config
from helpers.agent_tools import TOOL_SCHEMAS, execute_tool
from helpers.scope_token import verify_scope_token, ScopeTokenError
from helpers.project_scope import (
    project_member_study_ids,
    project_scoped_search_studies,
    project_scoped_search_by_sample,
    enforce_project_get_report,
    enforce_project_pin,
)
from store import get_project, get_project_studies_only

logger = logging.getLogger(__name__)

_KNOWN_TOOLS = {"search_studies", "get_study_report", "pin_study", "search_by_sample"}


def _guard():
    """Shared gate for every internal tool request: source-IP allowlist, then
    the shared secret. Returns an error response, or None to proceed.

    Applies to schema reads as well as tool calls — an attacker who can read
    the schemas learns the exact tool surface to aim a stolen scope token at,
    so there is no reason to guard that endpoint more weakly."""
    allowed = config.PI_ALLOWED_TOOL_CALLERS
    if allowed and request.remote_addr not in allowed:
        logger.warning("internal tool call from disallowed origin %s", request.remote_addr)
        return jsonify({'error': 'unauthorized'}), 401

    secret = config.PI_SIDECAR_SECRET or ""
    if not secret or not hmac.compare_digest(request.headers.get('X-Pi-Secret', ''), secret):
        return jsonify({'error': 'unauthorized'}), 401
    return None


@app.route('/api/internal/tools/schemas', methods=['GET'])
def api_internal_tool_schemas():
    denied = _guard()
    if denied is not None:
        return denied
    return jsonify({'tools': TOOL_SCHEMAS})


def _serialize(result):
    return jsonify({
        'text': result.text,
        'label': result.label,
        'detail': result.detail,
        'ui_payload': result.ui_payload,
    })


def _run_project_scoped(name, args, claims):
    """Enforce the hard workspace boundary, then either resolve locally
    (search_*, which never touch execute_tool's global search) or fall
    through to execute_tool() with args already validated/trimmed to
    project members (get_study_report, pin_study)."""
    project_id = claims.get('project_id')
    # Independent of whatever minted this token: re-check ownership here too,
    # since get_project_studies_only() itself performs no user_id filtering
    # (store/crud.py) — this route must not simply trust the token's claims.
    if not get_project(project_id, claims['user_id']):
        return jsonify({'error': 'project not found'}), 404
    proj = get_project_studies_only(project_id)
    if not proj:
        return jsonify({'error': 'project not found'}), 404
    member_ids = project_member_study_ids(proj)
    studies = proj.get('studies') or []

    if name == 'search_studies':
        return _serialize(project_scoped_search_studies(args, studies))
    if name == 'search_by_sample':
        return _serialize(project_scoped_search_by_sample(args, studies))
    if name == 'get_study_report':
        refusal = enforce_project_get_report(args, member_ids)
        if refusal is not None:
            return _serialize(refusal)
        return _serialize(execute_tool(name, args, scope='project', chat_id=claims['chat_id']))
    if name == 'pin_study':
        refusal = enforce_project_pin(args, member_ids)
        if refusal is not None:
            return _serialize(refusal)
        dropped = args.pop('_dropped_out_of_scope', None)
        result = execute_tool(name, args, scope='project', chat_id=claims['chat_id'])
        if dropped:
            note = f"({len(dropped)} of the requested studies are not in this project's workspace and were skipped: {dropped}.) "
            result.text = note + result.text
        return _serialize(result)
    return jsonify({'error': f'unknown tool: {name}'}), 404


@app.route('/api/internal/tools/<name>', methods=['POST'])
def api_internal_tool_call(name):
    denied = _guard()
    if denied is not None:
        return denied

    if name not in _KNOWN_TOOLS:
        return jsonify({'error': f'unknown tool: {name}'}), 404

    token = request.headers.get('X-Tool-Token', '')
    try:
        claims = verify_scope_token(token)
    except ScopeTokenError as exc:
        logger.warning("rejected internal tool call token: %s", exc)
        return jsonify({'error': 'invalid or expired tool token'}), 401

    args = request.get_json(silent=True) or {}

    if claims['scope'] == 'project':
        return _run_project_scoped(name, args, claims)

    result = execute_tool(
        name, args,
        scope='global', chat_id=claims['chat_id'],
        deep_search=bool(claims.get('deep_search')),
    )
    return _serialize(result)
