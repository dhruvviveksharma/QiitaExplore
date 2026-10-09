"""GET /api/chat-exports/<id>.<csv|tsv> — a CSV / TSV file made from a chat
(helpers/export_tools.py), for its owner only. samples / files exports are
rebuilt from Qiita at each download; a model-written table is served as stored."""
import logging

from flask import Response, g, jsonify

from run import app
from store import get_export
from helpers.export_tools import FORMATS, build_table, serialize
from helpers.qiita_fetch import is_study_public

logger = logging.getLogger(__name__)


@app.route("/api/chat-exports/<export_id>.<ext>", methods=["GET"])
def download_chat_export(export_id, ext):
    if ext not in FORMATS:
        return jsonify({"error": "export must be csv or tsv"}), 404
    export = get_export(export_id, g.user_id)
    if not export:
        return jsonify({"error": "Export not found"}), 404
    sid = export["spec"].get("study_id")
    if sid is not None and not is_study_public(sid):
        return jsonify({"error": "That study is no longer public"}), 404
    try:
        columns, rows = build_table(export)
    except Exception:
        logger.exception("building chat export %s failed", export_id)
        return jsonify({"error": "Could not build the export"}), 500
    return Response(serialize(columns, rows, ext), mimetype=FORMATS[ext][0],
                    headers={"Content-Disposition": f'attachment; filename="{export["name"]}.{ext}"'})
