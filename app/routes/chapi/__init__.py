from flask import Blueprint, render_template, url_for, current_app, session, redirect, jsonify, request
import asyncio
import json
import uuid
from app.services import AskarStorage, WorkflowManager, AgentController
from app.services.vc_playground import CredentialHandler
from app.utils import return_list_if_object
from config import Config

bp = Blueprint("wallet", __name__)

askar = AskarStorage()
agent = AgentController()
handler = CredentialHandler()


@bp.before_request
def before_request_callback():
    session['endpoint'] = Config.ENDPOINT
    if not session.get('did_auth', None):
        exchange_url = WorkflowManager().create_did_auth()
        exchange_id = exchange_url.split('/')[-1]
        session['did_auth'] = {
            'exchange_id': exchange_url.split('/')[-1],
            'exchange_url': exchange_url
        }
        current_app.logger.warning(f"Created DID Auth: {exchange_id}")
    else:
        exchange_id = session['did_auth']['exchange_url'].split('/')[-1]
        current_app.logger.warning(f"Current DID Auth: {exchange_id}")


@bp.route("/handler", methods=["GET"])
def wallet_handler():
    current_app.logger.warning("CHAPI handler()")
    return render_template(
        "components/chapi/handler.jinja",
        title=current_app.config['APP_NAME'],
        page_title='Wallet'
    )


@bp.route("/login", methods=["POST"])
def login():
    current_app.logger.warning("CHAPI login()")
    exchange_id = session['did_auth']['exchange_url'].split('/')[-1]
    current_app.logger.warning(f"Checking DID Auth: {exchange_id}")
    exchange_state = asyncio.run(
        askar.fetch('workflow/authentication', exchange_id)
    )
    session['did'] = session['did_auth']['holder_did'] = exchange_state.get('holder')
    session['client_id'] = session['did_auth']['client_id'] = asyncio.run(
        askar.fetch_name_by_tag('wallet', {
            "dids": session['did_auth']['holder_did']
        })
    )
    if session['did_auth'].get('client_id'):
        return jsonify({'client_id': session['did_auth'].get('client_id')})
    
    return "Login failed", 400


@bp.route("/logout", methods=["POST"])
def logout():
    current_app.logger.warning("CHAPI logout()")
    session.clear()
    return "Logged out", 200


@bp.route("/get", methods=["GET", "POST"])
def get():
    current_app.logger.warning("CHAPI get()")
    client_id = session['did_auth'].get('client_id')
    
    if request.method == "POST":
        
        if request.form['client_id'] != client_id:
            return jsonify({'status': 'failed'})
        
        query = json.loads(request.form['payload'])
        vp = asyncio.run(handler.query_response(query))
        return jsonify(vp)
    
    return render_template(
        "components/chapi/get.jinja",
        title=current_app.config['APP_NAME'],
        page_title='Wallet'
    )


@bp.route("/store", methods=["GET", "POST"])
def store():
    current_app.logger.warning("CHAPI store()")
    client_id = session['did_auth'].get('client_id')
    
    if request.method == "POST":
        
        if request.form['client_id'] != client_id:
            return jsonify({'status': 'failed'})
        
        vp = json.loads(request.form['payload'])
        vp['verifiableCredential'] = return_list_if_object(vp['verifiableCredential'])
        
        for vc in vp['verifiableCredential']:
            asyncio.run(agent.request_token(client_id))
            asyncio.run(agent.store_vc(vc))

    return render_template(
        "components/chapi/store.jinja",
        title=current_app.config['APP_NAME'],
        page_title='Wallet'
    )


@bp.route("/credentials", methods=["POST"])
def credentials():
    current_app.logger.warning("CHAPI credentials()")
    client_id = session['did_auth'].get('client_id')
    
    if request.form['client_id'] != client_id:
        current_app.logger.warning('Wrong Client Id')
        return jsonify({'status': 'failed'})

    credential_choices = {}
    credentials = asyncio.run(askar.fetch('credentials', client_id))
    
    for credential in credentials:
        credential_id = credential.get('id') or str(uuid.uuid4())
        credential_choices[credential_id] = credential
    
    current_app.logger.warning('Credentials count: ' + str(len(credentials)))
    
    return jsonify(credential_choices)