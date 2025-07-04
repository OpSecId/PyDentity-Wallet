from flask import current_app
import httpx
from config import Config
from app.utils import url_encode
from app.plugins.askar import AskarStorage

askar = AskarStorage()


class AgentController:
    def __init__(self):
        self.admin_endpoint = Config.AGENT_ADMIN_ENDPOINT
        self.admin_headers = {"X-API-KEY": Config.AGENT_ADMIN_API_KEY}
        self.tenant_headers = {}

    def _try_return(self, response):
        try:
            return response.json()
        except:
            current_app.logger.warning(response.status_code)
            current_app.logger.warning(response.text)
            return None

    def create_subwallet(self, client_id, wallet_key):
        current_app.logger.info(f"Creating new Subwallet: {client_id}")
        r = httpx.post(
            f"{self.admin_endpoint}/multitenancy/wallet",
            json={
                "label": f"{Config.APP_NAME} - {client_id}",
                # "image_url": f'{Config.AVATAR_URL}?seed={client_id}',
                "wallet_key": wallet_key,
                "wallet_name": client_id,
                "wallet_type": "askar-anoncreds",
                "key_management_mode": "managed",
            },
            headers=self.admin_headers,
        )
        return self._try_return(r)

    def request_token(self, wallet_id, wallet_key):
        current_app.logger.warning("Requesting Access Token")
        r = httpx.post(
            f"{self.admin_endpoint}/multitenancy/wallet/{wallet_id}/token",
            json={"wallet_key": wallet_key},
            headers=self.admin_headers,
        )
        return self._try_return(r)

    def set_token(self, token):
        self.tenant_headers["Authorization"] = f"Bearer {token}"

    async def set_agent_auth(self, wallet_id):
        wallet = await askar.fetch("wallet", wallet_id)
        self.set_token(wallet["token"])
        r = httpx.get(
            f"{self.admin_endpoint}/wallet/did",
            headers=self.tenant_headers,
        )
        if r.status_code == 401:
            wallet["token"] = self.request_token(wallet_id, wallet["wallet_key"]).get(
                "token"
            )
            self.set_token(wallet["token"])
        await askar.update("wallet", wallet_id, wallet)

    def create_key(self):
        current_app.logger.warning("Creating keypair")
        r = httpx.post(
            f"{self.admin_endpoint}/wallet/keys",
            json={"alg": "ed25519"},
            headers=self.tenant_headers,
        )
        return self._try_return(r)

    def create_did(self, method="key", key_type="ed25519"):
        current_app.logger.warning("Creating did")
        r = httpx.post(
            f"{self.admin_endpoint}/wallet/did/create",
            json={"method": method, "options": {"key_type": key_type}},
            headers=self.tenant_headers,
        )
        return self._try_return(r)

    def receive_invitation(self, invitation):
        current_app.logger.warning("Receiving invitation.")
        label = invitation.get("label")
        r = httpx.post(
            f"{self.admin_endpoint}/out-of-band/receive-invitation?alias={label}",
            json=invitation,
            headers=self.tenant_headers,
        )
        return self._try_return(r)

    def get_connections(self):
        r = httpx.get(f"{self.admin_endpoint}/connections", headers=self.tenant_headers)
        return self._try_return(r)

    def get_connection_info(self, connection_id):
        r = httpx.get(
            f"{self.admin_endpoint}/connections/{connection_id}",
            headers=self.tenant_headers,
        )
        return self._try_return(r)

    def get_schema_info(self, schema_id):
        r = httpx.get(
            f"{self.admin_endpoint}/anoncreds/schema/{url_encode(schema_id)}",
            headers=self.tenant_headers,
        )
        return self._try_return(r)

    def get_credentials(self):
        r = httpx.get(f"{self.admin_endpoint}/credentials", headers=self.tenant_headers)
        return self._try_return(r)

    def get_w3c_credentials(self):
        r = httpx.post(
            f"{self.admin_endpoint}/credentials/w3c",
            headers=self.tenant_headers,
            json={},
        )
        return self._try_return(r)

    def store_credential(self, vc):
        r = httpx.post(
            f"{self.admin_endpoint}/vc/credentials/store",
            headers=self.tenant_headers,
            json={"verifiableCredential": vc},
        )
        return self._try_return(r)

    def sign_presentation(self, presentation, options):
        r = httpx.post(
            f"{self.admin_endpoint}/vc/presentations/prove",
            headers=self.tenant_headers,
            json={"presentation": presentation, "options": options},
        )
        return self._try_return(r)