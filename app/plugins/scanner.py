import requests
from flask import current_app
from app.plugins.vcapi import VcApiExchanger, exchange_to_client_response
from app.plugins.acapy import AgentController
from app.plugins.askar import AskarStorage, AskarStorageKeys
import json
import base64
import logging
import uuid

from urllib.parse import urlparse, unquote, parse_qs

agent = AgentController()
logger = logging.getLogger(__name__)

JSON_HEADERS = {"Accept": "application/json"}
VCPLAYGROUND_HOST = "vcplayground.org"
# VCALM / VC API exchange keys — preferred over OID4VCI, OID4VP, etc.
VCALM_PROTOCOL_KEYS = ("vcapi", "vcalm")


def _log(message: str, level: int = logging.INFO) -> None:
    try:
        current_app.logger.log(level, message)
    except RuntimeError:
        logger.log(level, message)


def _log_json(label: str, data: object, level: int = logging.INFO) -> None:
    try:
        payload = json.dumps(data, indent=2, default=str)
    except TypeError:
        payload = repr(data)
    if len(payload) > 4000:
        payload = payload[:4000] + "\n... (truncated)"
    _log(f"{label}:\n{payload}", level)


def _log_http_response(label: str, response: requests.Response) -> None:
    _log(f"{label}: HTTP {response.status_code} {response.reason}")
    try:
        body = response.json()
        _log_json(f"{label} body keys", list(body.keys()) if isinstance(body, dict) else body)
    except ValueError:
        text = response.text or ""
        _log(f"{label} body (non-JSON): {text[:500]}{'...' if len(text) > 500 else ''}")


def is_iuv_url(url: str) -> bool:
    return parse_qs(urlparse(url).query).get("iuv", [None])[0] == "1"


def decode_interaction_url(interaction_url: str) -> str:
    """Resolve the VC API exchange URL from an IUV interaction URL."""
    parsed = urlparse(interaction_url)
    if parsed.netloc.endswith(VCPLAYGROUND_HOST) and parsed.path.startswith("/interactions/"):
        encoded = parsed.path[len("/interactions/") :]
        exchange_url = unquote(encoded).rstrip("/")
        _log(f"Decoded VC Playground interaction URL → {exchange_url}")
        return exchange_url
    exchange_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
    _log(f"Using direct exchange URL (no playground wrapper): {exchange_url}")
    return exchange_url


def fetch_protocols(exchange_url: str) -> dict:
    """GET {exchangeUrl}/protocols per VCALM / VC API exchange discovery."""
    protocols_url = f"{exchange_url.rstrip('/')}/protocols"
    _log(f"GET {protocols_url} (Accept: application/json)")
    response = requests.get(protocols_url, headers=JSON_HEADERS, timeout=30)
    _log_http_response("Protocol discovery", response)
    response.raise_for_status()
    body = response.json()
    protocols = body.get("protocols")
    if not protocols:
        raise ValueError("Protocols response missing 'protocols' object")
    _log_json("Discovered protocols", protocols)
    return protocols


def discover_protocols(interaction_url: str) -> dict:
    """Decode interaction URL when needed, then discover supported protocols."""
    _log(f"IUV discovery starting for {interaction_url}")
    exchange_url = decode_interaction_url(interaction_url)
    try:
        return fetch_protocols(exchange_url)
    except (requests.RequestException, ValueError) as err:
        _log(
            f"GET {exchange_url}/protocols failed ({err}); falling back to interaction URL",
            logging.WARNING,
        )
        _log(f"GET {interaction_url} (Accept: application/json)")
        response = requests.get(interaction_url, headers=JSON_HEADERS, timeout=30)
        _log_http_response("Interaction URL discovery", response)
        response.raise_for_status()
        body = response.json()
        protocols = body.get("protocols")
        if not protocols:
            raise ValueError("Interaction URL response missing 'protocols' object") from err
        _log_json("Discovered protocols (fallback)", protocols)
        return protocols


def select_vcalm_exchange(protocols: dict) -> tuple[str, str] | None:
    """Pick the VCALM/VC API exchange URL from a protocols discovery object."""
    normalized = {key.lower(): value for key, value in protocols.items()}
    skipped = []
    for key, value in protocols.items():
        if key.lower() not in VCALM_PROTOCOL_KEYS:
            skipped.append(key)
    if skipped:
        _log(f"IUV protocol selection skipping: {', '.join(skipped)}")
    for preferred in VCALM_PROTOCOL_KEYS:
        url = normalized.get(preferred)
        if isinstance(url, str) and url.startswith("https://"):
            _log(f"IUV protocol selected: {preferred} → {url.rstrip('/')}")
            return preferred, url.rstrip("/")
    _log("No vcapi/vcalm HTTPS exchange URL found in protocols", logging.WARNING)
    return None


class QRScanner:
    def __init__(self, wallet_id):
        self.wallet_id = wallet_id
        self.askar = AskarStorage.for_wallet(wallet_id)

    async def handle_payload(self, payload):
        _log(f"QR scanner payload received (wallet={self.wallet_id}): {payload[:200]}{'...' if len(payload) > 200 else ''}")
        uri = urlparse(payload)
        _log(f"Parsed URI scheme={uri.scheme} host={uri.netloc} query={uri.query}")
        
        if uri.scheme == "https":
            if is_iuv_url(payload):
                _log("Matched IUV interaction URL (iuv=1)")
                result = await self.iuv_handler(payload)
                _log_json("IUV handler result", result)
                return {"type": "iuv", "result": result}
                
            elif uri.query.startswith("oob"):
                current_app.logger.info("Out of band invitation")
                invitation = payload.split('?oob=')[-1]
                decoded_invitation = json.loads(base64.urlsafe_b64decode(invitation+'===').decode())
                await self.didcomm_handler(decoded_invitation)
                return {"type": "oob_invitation", "label": decoded_invitation.get("label", "Unknown")}
                
            elif payload.split('?')[-1].startswith('_oobid='):
                try:
                    r = requests.get(payload)
                    invitation = r.json()
                    await self.didcomm_handler(invitation)
                except:
                    current_app.logger.info(r.text)
        
        current_app.logger.info("No matching URL scheme found")
        return {"type": "unknown", "message": "No matching URL scheme found"}
    
    async def didcomm_handler(self, invitation):
        # BUG: marshmallow.exceptions.ValidationError: {'_schema': ['Model cannot have goal_code without goal']}
        if invitation.get('goal_code') and not invitation.get('goal'):
            invitation.pop('goal_code')
            
        current_app.logger.info(invitation)
        if invitation.get('@type') and invitation.get('@type').startswith('https://didcomm.org/out-of-band/1.'):
            if (wallet := await self.askar.fetch(AskarStorageKeys.WALLETS)):
                agent.set_token(wallet['token'])
                agent.receive_invitation(invitation)

    async def iuv_handler(self, payload):
        _log("Starting IUV / VCALM exchange handler")
        protocols = discover_protocols(payload)

        selected = select_vcalm_exchange(protocols)
        if not selected:
            result = {
                "status": "unsupported",
                "protocols": list(protocols.keys()),
                "message": "No vcapi/vcalm protocol in discovery response",
            }
            _log_json("IUV handler finished (unsupported)", result, logging.WARNING)
            return result

        protocol_name, exchange_url = selected
        exchange_id = str(uuid.uuid4())
        vcapi = VcApiExchanger(self.wallet_id, exchange_url, protocol=protocol_name)
        exchange_state = await vcapi.start_exchange(exchange_id)
        loop_result = exchange_to_client_response(exchange_state)
        result = {
            "protocol": protocol_name,
            "exchangeUrl": exchange_url,
            **loop_result,
        }
        _log_json("IUV handler finished", result)
        return result
