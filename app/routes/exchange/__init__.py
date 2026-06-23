from asyncio import run as await_

from flask import Blueprint, jsonify, redirect, session, url_for

from app.plugins.vcalm_exchange_store import VcalmExchangeStore
from app.plugins.vcapi import VcApiExchanger, exchange_to_client_response

bp = Blueprint("exchange", __name__, url_prefix="/exchange")


@bp.before_request
def before_request_callback():
    if not session.get("client_id"):
        return redirect(url_for("auth.index"))


def _load_exchange(exchange_id: str):
    wallet_id = session.get("wallet_id")
    if not wallet_id:
        return None, (jsonify({"status": "error", "message": "No wallet in session"}), 401)

    store = VcalmExchangeStore(wallet_id)
    exchange = await_(store.load(exchange_id))
    if not exchange:
        return None, (
            jsonify({"status": "error", "message": "Exchange not found or expired"}),
            404,
        )
    return (exchange, store), None


def _respond(exchange, store: VcalmExchangeStore):
    if exchange.status in ("complete", "abandoned", "error"):
        await_(store.delete(exchange.id))
    elif exchange.status in (
        "awaiting_presentation_consent",
        "awaiting_store_consent",
    ):
        await_(store.persist(exchange))

    return jsonify(
        {
            "status": "success",
            "result": exchange_to_client_response(exchange),
        }
    )


@bp.route("/<exchange_id>", methods=["GET"])
def get_exchange(exchange_id):
    loaded, error = _load_exchange(exchange_id)
    if error:
        return error
    exchange, _store = loaded
    return jsonify({"status": "success", "result": exchange_to_client_response(exchange)})


@bp.route("/<exchange_id>/present", methods=["POST"])
def confirm_presentation(exchange_id):
    loaded, error = _load_exchange(exchange_id)
    if error:
        return error
    exchange, store = loaded

    if exchange.status != "awaiting_presentation_consent":
        return jsonify({"status": "error", "message": "No presentation pending"}), 400

    vcapi = VcApiExchanger(
        exchange.wallet_id, exchange.exchange_url, protocol=exchange.protocol
    )
    exchange = await_(vcapi.confirm_presentation(exchange))
    return _respond(exchange, store)


@bp.route("/<exchange_id>/decline-presentation", methods=["POST"])
def decline_presentation(exchange_id):
    loaded, error = _load_exchange(exchange_id)
    if error:
        return error
    exchange, store = loaded

    vcapi = VcApiExchanger(
        exchange.wallet_id, exchange.exchange_url, protocol=exchange.protocol
    )
    exchange = await_(vcapi.abandon(exchange))
    return _respond(exchange, store)


@bp.route("/<exchange_id>/store", methods=["POST"])
def confirm_store(exchange_id):
    loaded, error = _load_exchange(exchange_id)
    if error:
        return error
    exchange, store = loaded

    if exchange.status != "awaiting_store_consent":
        return jsonify({"status": "error", "message": "No credential pending storage"}), 400

    vcapi = VcApiExchanger(
        exchange.wallet_id, exchange.exchange_url, protocol=exchange.protocol
    )
    exchange = await_(vcapi.confirm_store(exchange))
    return _respond(exchange, store)


@bp.route("/<exchange_id>/decline-store", methods=["POST"])
def decline_store(exchange_id):
    loaded, error = _load_exchange(exchange_id)
    if error:
        return error
    exchange, store = loaded

    vcapi = VcApiExchanger(
        exchange.wallet_id, exchange.exchange_url, protocol=exchange.protocol
    )
    exchange = await_(vcapi.skip_store(exchange))
    return _respond(exchange, store)
