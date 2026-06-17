from flask import (
    Blueprint,
    current_app,
    request,
    jsonify,
    render_template,
    session,
    redirect,
    url_for,
    Response,
    stream_with_context,
)
from app.plugins import QRScanner, AskarStorage, AskarStorageKeys
from app.operations import sync_session, sign_in_agent
from app.utils import notification_broadcaster, is_mobile
from asyncio import run as await_
import json
import os


bp = Blueprint("main", __name__)


@bp.before_request
def before_request_callback():
    # Allow desktop users to see install page without authentication
    if request.endpoint == 'main.index' and not is_mobile():
        return None
    
    if not session.get("client_id"):
        return redirect(url_for("auth.index"))


@bp.route("/", methods=["GET"])
def index():
    # Check if user is on desktop - show install page
    if not is_mobile():
        # Use ngrok URL if available, otherwise use request host URL
        from config import Config
        app_url = current_app.config.get('NGROK_URL') or request.host_url.rstrip('/')
        demo_url = Config.DEMO_ANONCREDS
        project_url = "https://github.com/OpenWallet-Foundation-Labs/pydentity-wallet"
        return render_template(
            "pages/install.jinja",
            app_url=app_url,
            demo_url=demo_url,
            project_url=project_url
        )
    
    # Mobile users - proceed with wallet interface
    try:
        await_(sync_session(session.get("client_id")))
    except ValueError:
        # Profile doesn't exist yet (incomplete registration)
        session.clear()
        return redirect(url_for("auth.index"))
    return render_template("pages/index.jinja")


@bp.route("/notifications/stream")
def notification_stream():
    """Server-Sent Events endpoint for real-time notifications"""
    wallet_id = session.get("wallet_id")
    
    if not wallet_id:
        return jsonify({"error": "No wallet_id in session"}), 401
    
    @stream_with_context
    def generate():
        # Subscribe to notifications for this wallet
        q = notification_broadcaster.subscribe(wallet_id)
        
        try:
            # Send initial connection message
            yield f"data: {json.dumps({'type': 'connected', 'wallet_id': wallet_id})}\n\n"
            
            # Keep connection alive and send events
            while True:
                try:
                    # Wait for event with timeout to send keepalive
                    import queue as queue_module
                    event = q.get(timeout=30)
                    yield f"data: {json.dumps(event)}\n\n"
                except queue_module.Empty:
                    # Send keepalive comment
                    yield ": keepalive\n\n"
                except Exception as e:
                    current_app.logger.debug(f"Error in SSE stream: {e}")
                    break
        except GeneratorExit:
            # Client disconnected gracefully
            current_app.logger.debug(f"SSE client disconnected: {wallet_id}")
        except Exception as e:
            current_app.logger.warning(f"SSE stream error for {wallet_id}: {e}")
        finally:
            notification_broadcaster.unsubscribe(wallet_id, q)
    
    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive'
        }
    )


@bp.route("/scanner", methods=["POST"])
def scan_qr_code():
    payload = request.form.get("payload", "")
    current_app.logger.info(
        f"QR scan request wallet={session.get('wallet_id')} payload_len={len(payload)}"
    )
    try:
        result = await_(
            QRScanner(session["wallet_id"]).handle_payload(payload)
        )
    except Exception as err:
        current_app.logger.error(f"QR scan failed: {err}", exc_info=True)
        return jsonify({"status": "error", "message": str(err)}), 500

    current_app.logger.info(f"QR scan result type={result.get('type')} status={result.get('result', {}).get('status')}")
    
    # Return success with message about what was processed
    return jsonify({
        "status": "success", 
        "message": "QR code processed successfully",
        "result": result
    })


@bp.route("/connection-status", methods=["GET"])
def get_connection_status():
    """Get the current connection state"""
    from app.plugins import AgentController, AskarStorage, AskarStorageKeys
    from asyncio import run as await_
    
    current_app.logger.info("=== Connection Status Check ===")
    
    try:
        # Get wallet_id from session
        wallet_id = session.get("wallet_id")
        current_app.logger.info(f"Wallet ID from session: {wallet_id}")
        
        if not wallet_id:
            current_app.logger.warning("No wallet_id in session")
            return jsonify({"state": "no_wallet", "connected": False})
        
        # Get the latest connection ID from session or most recent connection
        wallet_askar = AskarStorage.for_wallet(wallet_id)
        connections = await_(wallet_askar.fetch(AskarStorageKeys.CONNECTIONS)) or []
        current_app.logger.info(f"Found {len(connections)} connections in session")
        
        if not connections:
            current_app.logger.warning("No connections found in session")
            return jsonify({"state": "no_connection", "connected": False})
        
        # Get the most recent connection
        latest_connection = connections[-1] if connections else {}
        current_app.logger.info(f"Latest connection: {latest_connection}")
        
        state = latest_connection.get('state', 'unknown')
        label = latest_connection.get('their_label', 'Unknown')
        
        current_app.logger.info(f"Connection state: {state}, Their label: {label}")
        
        return jsonify({
            "state": state,
            "label": label
        })
    except Exception as e:
        current_app.logger.error(f"Error getting connection status: {e}", exc_info=True)
        return jsonify({"state": "error", "connected": False, "error": str(e)})


@bp.route("/connections/<connection_id>", methods=["GET"])
def get_connection_details(connection_id):
    """Get connection details and messages"""
    wallet_id = session.get("wallet_id")
    wallet_askar = AskarStorage.for_wallet(wallet_id)
    
    try:
        # Fetch connection
        connections = await_(wallet_askar.fetch(AskarStorageKeys.CONNECTIONS)) or []
        connection = next((c for c in connections if c.get('connection_id') == connection_id), None)
        
        if not connection:
            return jsonify({"error": "Connection not found"}), 404
        
        # Fetch messages for this connection
        messages = await_(wallet_askar.fetch(AskarStorageKeys.MESSAGES)) or []
        # Filter messages by connection_id if we can determine it
        # For now, just return all messages (can be filtered later if needed)
        connection_messages = messages
        
        return jsonify({
            "connection": connection,
            "messages": connection_messages
        })
    except Exception as e:
        current_app.logger.error(f"Error getting connection details: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


