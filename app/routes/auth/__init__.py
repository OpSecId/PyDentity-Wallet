from flask import (
    Blueprint,
    render_template,
    session,
    redirect,
    url_for,
    request,
    abort,
    jsonify,
    current_app,
)
from config import Config
from app.plugins import AgentController, AskarStorage, WebAuthnProvider, AskarStorageKeys
from app.operations import provision_wallet
from app.utils import is_mobile
from webauthn.helpers.exceptions import (
    InvalidRegistrationResponse,
    InvalidAuthenticationResponse,
)
from asyncio import run as await_
import uuid
import json

bp = Blueprint("auth", __name__)
agent = AgentController()
webauthn = WebAuthnProvider()


def _prime_auth_session():
    session["endpoint"] = Config.APP_URL
    session["development"] = Config.TESTING
    session["app_icon"] = Config.APP_ICON
    session["app_logo"] = Config.APP_LOGO


@bp.route("/")
def index():
    session.clear()
    _prime_auth_session()
    if Config.ENV == "development":
        # For development, we bypass the webauthn auth flow
        session["client_id"] = str(uuid.uuid4())
        wallet = await_(provision_wallet(session.get("client_id")))
        session["token"], session["wallet_id"] = (
            wallet.get("token"),
            wallet.get("wallet_id"),
        )
        return redirect(url_for("main.index"))
    if not is_mobile():
        return redirect(url_for("install"))
    return render_template("pages/landing.jinja", title=Config.APP_NAME)


@bp.route("/sign-in")
def sign_in():
    _prime_auth_session()
    if not is_mobile() and Config.ENV != "development":
        return redirect(url_for("install"))
    return render_template("pages/auth.jinja", title=Config.APP_NAME, mode="sign-in")


@bp.route("/create")
def create_wallet():
    _prime_auth_session()
    if not is_mobile() and Config.ENV != "development":
        return redirect(url_for("install"))
    return render_template("pages/auth.jinja", title=Config.APP_NAME, mode="create")


@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        session["client_id"] = client_id = str(uuid.uuid4())
        registration_credential = await_(
            webauthn.prepare_credential_creation(client_id, "PyDentity")
        )
        return jsonify(registration_credential), 200

    elif request.method == "POST":
        if not session.get("client_id"):
            abort("Error user not found", 400)

        current_app.logger.warning(f"Prepare registration: {session.get('client_id')}")

        registration_credential = await_(
            webauthn.create_registration_credential(json.loads(request.get_data()))
        )
        try:
            await_(
                webauthn.verify_and_save_credential(
                    session.get("client_id"), registration_credential
                )
            )

            wallet = await_(provision_wallet(session.get("client_id")))
            session["token"], session["wallet_id"] = (
                wallet.get("token"),
                wallet.get("wallet_id"),
            )

            return jsonify(
                {"verified": True, "client_id": session.get("client_id")}
            ), 201

        except InvalidRegistrationResponse:
            abort(jsonify({"verified": False}), 400)
    return redirect(url_for("auth.logout"))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if not request.args.get("client_id"):
        return redirect(url_for("auth.logout"))

    client_id = request.args.get("client_id")

    if request.method == "GET":
        current_app.logger.warning(f"Prepare login: {client_id}")
        auth_options = await_(webauthn.prepare_login_with_credential(client_id))
        if not auth_options:
            current_app.logger.warning(f"No wallet credentials found for client: {client_id}")
            return jsonify({
                "verified": False,
                "error": "wallet_not_found",
                "message": "No wallet found for this device. Please register again.",
            }), 404
        return jsonify(auth_options), 200

    elif request.method == "POST":
        current_app.logger.warning(f"Verify login: {client_id}")
        
        # Fetch profile from global storage
        global_askar = AskarStorage.global_store()
        profile = await_(global_askar.fetch(AskarStorageKeys.PROFILES, client_id))
        if not profile:
            abort(jsonify({"verified": False}), 400)
        
        # Fetch wallet from wallet-specific storage
        wallet_id = profile.get("wallet_id")
        wallet_askar = AskarStorage.for_wallet(wallet_id)
        wallet = await_(wallet_askar.fetch(AskarStorageKeys.WALLETS))
        if not wallet:
            abort(jsonify({"verified": False}), 400)

        attestation = json.loads(request.get_data())
        try:
            await_(webauthn.verify_authentication_credential(client_id, attestation))
            session["token"] = wallet["token"] = agent.request_token(
                wallet.get("wallet_id"),
                wallet.get("wallet_key"),
            ).get("token")
            await_(wallet_askar.update(AskarStorageKeys.WALLETS, "data", wallet))

            session["client_id"], session["wallet_id"] = client_id, wallet["wallet_id"]

            return jsonify({"verified": True}), 200

        except InvalidAuthenticationResponse:
            abort(jsonify({"verified": False}), 400)

    return redirect(url_for("auth.logout"))


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.index"))
