from flask import current_app
import requests
import json
import base64
from app.plugins import AgentController, AskarStorage
from app.utils import decode_jwt_vc, force_array_to_dict
from urllib.parse import urlparse, unquote

askar = AskarStorage()
agent = AgentController()


def decode_invitation(data):
    return json.loads(base64.urlsafe_b64decode(data + "===").decode())


class QRScanner:
    def __init__(self, client_id, wallet_id):
        self.client_id = client_id
        self.wallet_id = wallet_id

    async def handle_payload(self, payload):
        current_app.logger.warning("Parsing payload")
        uri = urlparse(payload)

        if uri.scheme == "https":
            query_string = payload.split("?")[-1]
            # Interaction URL Version
            if query_string.startswith("iuv=1"):
                pass
            
            # VC Playground interaction URI
            elif uri.netloc == "vcplayground.org":
                current_app.logger.warning("VC Playground")
                await self.vc_api_handler(unquote(uri.path.split("/")[-1]))

            else:
                await self.raw_url_handler(payload)
        
        return

    async def vc_api_handler(self, exchange_url):
        current_app.logger.warning("VC-API Exchange")
        wallet = await askar.fetch("wallet", self.wallet_id)
        profile = await askar.fetch("profile", self.client_id)
        exchange = requests.post(exchange_url, json={}).json()
        if exchange.get("verifiablePresentation"):
            vp = exchange.get("verifiablePresentation")
            for vc in vp["verifiableCredential"]:
                agent.set_token(wallet["token"])
                agent.store_credential(vc)
                await askar.append("credentials", self.wallet_id, vc)

        elif exchange.get("verifiablePresentationRequest"):
            credentials = await askar.fetch("credentials", self.wallet_id)
            pres_req = exchange.get("verifiablePresentationRequest")
            presentation = {
                "@context": ["https://www.w3.org/ns/credentials/v2"],
                "type": ["VerifiablePresentation"],
                "verifiableCredential": [],
            }
            options = {
                "proofType": "Ed25519Signature2020",
                "proofPurpose": "authentication",
                "domain": pres_req.get("domain"),
                "challenge": pres_req.get("challenge"),
            }
            for query in pres_req.get("query"):
                if query.get("type") == "QueryByExample":
                    cred_query = query.get("credentialQuery")
                    reason = cred_query.get("reason")
                    credential = await self.match_credential(
                        credentials,
                        cred_query.get("example"),
                        cred_query.get("acceptedCryptosuites"),
                    )
                    if credential:
                        presentation["verifiableCredential"].append(credential)
                elif query.get("type") == "DIDAuthentication":
                    key = profile.get("multikey")
                    presentation["holder"] = f"did:key:{key}"
                    options["verificationMethod"] = f"did:key:{key}#{key}"

            agent.set_token(wallet["token"])
            vp = agent.sign_presentation(presentation, options).get(
                "verifiablePresentation"
            )
            response = requests.post(exchange_url, json=vp)
            current_app.logger.warning(response.text)

    async def match_credential(self, credentials, example, cryptosuites):
        for credential in credentials:
            proof = credential.get("proof")
            if example.get("type")[0] in credential.get("type") and (
                proof.get("type") in cryptosuites
                or proof.get("cryptosuite") in cryptosuites
            ):
                return credential
        return None

    async def raw_url_handler(self, url):
        current_app.logger.warning("Unknown URI")

        current_app.logger.warning("Trying application/vc Simple Pickup ")
        r = requests.get(url, headers={"Accept": "application/vc"})
        credential = json.loads(r.text)
        credential["proof"] = force_array_to_dict(credential["proof"], 0)
        wallet = await askar.fetch("wallet", self.wallet_id)
        agent.set_token(wallet["token"])
        if agent.store_credential(credential).get("verifiableCredential"):
            await askar.append("credentials", self.wallet_id, credential)