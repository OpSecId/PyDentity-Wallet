from flask import Blueprint, render_template, session, redirect, url_for, jsonify, request
from app.operations import sync_session, sync_wallet
from asyncio import run as await_

from app.plugins import QRScanner

bp = Blueprint("main", __name__)


@bp.before_request
def before_request_callback():
    if not session.get('wallet_id'):
        return redirect(url_for("auth.index"))


@bp.route("/", methods=["GET"])
def index():
    await_(sync_session(session.get("wallet_id")))
    return render_template("pages/index.jinja")


@bp.route("/sync", methods=["GET"])
def sync():
    await_(sync_wallet(session.get("wallet_id")))
    return redirect(url_for("main.index"))


@bp.route("/scanner", methods=["POST"])
def scan_qr_code():
    qr_scanner = QRScanner(session["client_id"], session["wallet_id"])
    await_(qr_scanner.handle_payload(request.form["payload"]))
    return jsonify({"status": "ok"})