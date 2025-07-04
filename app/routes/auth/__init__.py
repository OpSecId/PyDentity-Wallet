from flask import Blueprint, render_template, session, redirect, url_for, request, abort, jsonify, current_app
from config import Config
from app.plugins import AgentController, AskarStorage, WebAuthnProvider
from app.operations import provision_wallet
from webauthn.helpers.exceptions import InvalidRegistrationResponse, InvalidAuthenticationResponse
from asyncio import run as await_
import uuid
import json

bp = Blueprint("auth", __name__)
agent = AgentController()
askar = AskarStorage()
webauthn = WebAuthnProvider()


@bp.route("/")
def index():
    session.clear()
    session["endpoint"] = Config.APP_URL
    session["development"] = Config.TESTING
    return render_template("pages/auth.jinja", title=Config.APP_NAME)

@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == 'GET':
        client_id = session['client_id'] = str(uuid.uuid4())
        current_app.logger.warning(f'New Registration Request: {client_id}')
        registration_credential = await_(webauthn.prepare_credential_creation(session['client_id'], 'PyDentity'))
        current_app.logger.warning('Registration Credential Prepared')
        return jsonify(registration_credential), 200

    elif request.method == 'POST':
        client_id = session.get("client_id")
        current_app.logger.warning(f'Checking Registration Request: {client_id}')
        if not client_id:
            abort("Error user not found", 400)

        registration_credential = await_(webauthn.create_registration_credential(json.loads(request.get_data())))
        current_app.logger.warning('Registration Credential Created')
        try:
            await_(webauthn.verify_and_save_credential(
                client_id, 
                registration_credential
            ))
            current_app.logger.warning('Registration Credential Verified')
            
            wallet = await_(provision_wallet(client_id))
            wallet_id = session['wallet_id'] = wallet.get('wallet_id')
            current_app.logger.warning(f'Wallet Provisioned: {wallet_id}')
            
            session['token'] = wallet.get('token')
            
            return jsonify(
                {
                    "verified": True,
                    "client_id": client_id
                }), 201
        
        except InvalidRegistrationResponse:
            current_app.logger.warning('Registration Credential Verification Failed')
            abort(jsonify({"verified": False}), 400)
    return redirect(url_for('auth.logout'))

@bp.route("/login", methods=["GET", "POST"])
def login():
    if not request.args.get('client_id'):
        return redirect(url_for('auth.logout'))
    
    client_id = request.args.get('client_id')

    if request.method == 'GET':
        current_app.logger.warning(f'Prepare login: {client_id}')
        auth_options = await_(webauthn.prepare_login_with_credential(client_id))
        return jsonify(auth_options), 200

    elif request.method == 'POST':
        current_app.logger.warning(f'Verify login: {client_id}')
        wallet = await_(askar.fetch('wallet', client_id))
        if not wallet:
            abort(jsonify({"verified": False}), 400)
            
        attestation = json.loads(request.get_data())
        try:
            await_(webauthn.verify_authentication_credential(client_id, attestation))
            wallet['token'] = agent.request_token(
                wallet.get('wallet_id'),
                wallet.get('wallet_key'),
            ).get('token')
            await_(askar.update('wallet', client_id, wallet))
            
            session['client_id'] = client_id
            session['token'] = wallet['token']
            
            return jsonify({"verified": True}), 200
        
        except InvalidAuthenticationResponse:
            abort(jsonify({"verified": False}), 400)

    return redirect(url_for('auth.logout'))


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.index"))
