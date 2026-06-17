import requests
import uuid
import logging
from datetime import datetime
from flask import current_app
from app.plugins.acapy import AgentController
from app.plugins.askar import AskarStorage, AskarStorageKeys
from app.models.notification import Notification
from app.utils import as_list, create_notification

MAX_EXCHANGE_STEPS = 10

agent = AgentController()
logger = logging.getLogger(__name__)


def _log(message: str, level: int = logging.INFO) -> None:
    try:
        current_app.logger.log(level, message)
    except RuntimeError:
        logger.log(level, message)


class VcApiExchanger:
    def __init__(self, wallet_id=None, exchange_url=None):
        self.wallet_id = wallet_id
        self.exchange_url = exchange_url
        self.askar = AskarStorage.for_wallet(wallet_id) if wallet_id else AskarStorage.global_store()
        _log(f"VcApiExchanger ready wallet={wallet_id} url={exchange_url}")

    def initiate_exchange(self):
        _log(f"POST {self.exchange_url} (initiate exchange)")
        response = requests.post(self.exchange_url, json={}, timeout=30)
        _log(f"Exchange initiate: HTTP {response.status_code}")
        try:
            body = response.json()
            if isinstance(body, dict):
                _log(f"Exchange response keys: {list(body.keys())}")
            response.raise_for_status()
            return body
        except ValueError:
            _log(f"Exchange response not JSON: {response.text[:500]}", logging.ERROR)
            response.raise_for_status()
            return {}

    async def store_credential(self, vp) -> int:
        vcs = as_list(
            vp.get("verifiableCredential"),
            label="verifiablePresentation.verifiableCredential",
        )
        if not vcs:
            _log("No verifiableCredential in exchange VP", logging.WARNING)
            return 0

        _log(f"Storing {len(vcs)} credential(s) from exchange VP")
        wallet = await self.askar.fetch(AskarStorageKeys.WALLETS)
        if not wallet:
            _log("Wallet record not found for credential storage", logging.ERROR)
            return 0

        stored = await self.askar.fetch(AskarStorageKeys.CREDENTIALS) or []
        agent.set_token(
            agent.request_token(self.wallet_id, wallet.get("wallet_key")).get("token")
        )

        for vc in vcs:
            # TODO, verify credential & remove unverifiable proofs
            agent.store_credential(vc)
            stored.append(vc)

            issuer = vc.get("issuer")
            origin = issuer if isinstance(issuer, str) else (issuer or {}).get("id", "")
            await create_notification(
                self.wallet_id,
                str(uuid.uuid4()),
                "vcapi_exchange",
                "Credential Stored",
                {
                    "origin": origin,
                    "message": "Credential stored from VCALM exchange",
                    "timestamp": datetime.now().isoformat(),
                },
            )

        await self.askar.update(AskarStorageKeys.CREDENTIALS, "data", stored)
        _log(f"Credential storage complete ({len(vcs)} stored)")
        return len(vcs)

    async def run_exchange(self) -> dict:
        """Run a VCALM/VC API exchange until complete or no further steps."""
        exchange = self.initiate_exchange()
        stored_credentials = 0
        presented = False

        for step in range(MAX_EXCHANGE_STEPS):
            if not isinstance(exchange, dict):
                break

            if exchange.get("verifiablePresentationRequest"):
                _log(f"Exchange step {step + 1}: verifiablePresentationRequest")
                follow_up = await self.present_credential(
                    exchange["verifiablePresentationRequest"]
                )
                presented = True
                if not isinstance(follow_up, dict):
                    return {
                        "status": "error",
                        "message": "Presentation failed",
                        "presented": presented,
                        "storedCredentials": stored_credentials,
                    }
                exchange = follow_up
                continue

            if exchange.get("verifiablePresentation"):
                _log(f"Exchange step {step + 1}: verifiablePresentation")
                stored_credentials += await self.store_credential(
                    exchange["verifiablePresentation"]
                )
                if exchange.get("verifiablePresentationRequest"):
                    continue
                return {
                    "status": "complete",
                    "presented": presented,
                    "storedCredentials": stored_credentials,
                }

            if exchange.get("redirectUrl"):
                return {
                    "status": "redirect",
                    "redirectUrl": exchange["redirectUrl"],
                    "presented": presented,
                    "storedCredentials": stored_credentials,
                }

            break

        return {
            "status": "complete",
            "presented": presented,
            "storedCredentials": stored_credentials,
        }

    async def present_credential(self, vpr):
        _log("Building presentation for VCALM exchange request")
        wallet = await self.askar.fetch(AskarStorageKeys.WALLETS)
        if not wallet:
            _log("Wallet record not found for presentation", logging.ERROR)
            return

        # Start building presentation object
        presentation = {
            "@context": ["https://www.w3.org/ns/credentials/v2"],
            "type": ["VerifiablePresentation"],
        }

        # Define proof options
        proof_options = {
            # TODO, how to select cryptosuite? Default to 'Ed25519Signature2020' for now
            "proofType": "Ed25519Signature2020",
            "domain": vpr.get("domain"),
            "challenge": vpr.get("challenge"),
            "proofPurpose": "authentication",
        }

        reason = None
        credentials = await self.askar.fetch(AskarStorageKeys.CREDENTIALS) or []
        for query in as_list(
            vpr.get("query"), label="verifiablePresentationRequest.query"
        ):
            if query.get("type") == "DIDAuthentication":
                _log("VPR query: DIDAuthentication")
                reason = reason or "Signed in with DID Authentication"
                methods = [
                    method.get("method")
                    for method in as_list(
                        query.get("acceptedMethods"), label="acceptedMethods"
                    )
                ]

                # TODO, only DID key for now, add support for did web
                if "key" not in methods:
                    return

                presentation["holder"] = wallet["holder_id"]
                multikey = presentation["holder"].split(":")[-1]
                proof_options["verificationMethod"] = f"did:key:{multikey}#{multikey}"

            if query.get("type") == "QueryByExample":
                _log("VPR query: QueryByExample")
                # We add the verifiableCredential property if not present
                if not presentation.get("verifiableCredential"):
                    presentation["verifiableCredential"] = []

                for cred_query in as_list(
                    query.get("credentialQuery"), label="credentialQuery"
                ):
                    # Check if the requested credential is required, ignore if optional...
                    if not cred_query.get("required", True):
                        continue

                    # TODO, process required trusted issuers
                    # trusted_issuers = cred_query.get('trustedIssuer', [])
                    reason = cred_query.get("reason")
                    example = cred_query.get("example")
                    accepted_cryptosuites = cred_query.get("acceptedCryptosuites")

                    # TODO, more comprehensive selection might be needed
                    vc = next(
                        (
                            credential
                            for credential in credentials
                            if set(example["type"]).issubset(credential["type"])
                            and set(example["@context"]).issubset(
                                credential["@context"]
                            )
                        ),
                        None,
                    )
                    if vc is None:
                        _log("No matching credential for QueryByExample", logging.WARNING)
                        continue

                    # We remove the proofs and force into an array
                    proofs = vc.pop("proof")
                    proofs = proofs if isinstance(proofs, list) else [proofs]

                    # We filter out proofs which don't have an accepted cryptosuite
                    for proof in proofs:
                        if (
                            proof.get("type") == "Ed25519Signature2020"
                            and "Ed25519Signature2020" not in accepted_cryptosuites
                        ):
                            proofs.remove(proof)
                        elif (
                            proof.get("type") == "DataIntegrityProof"
                            and proof.get("cryptosuite") not in accepted_cryptosuites
                        ):
                            proofs.remove(proof)

                    # We abandon the exchange if no proof is left
                    if len(proofs) == 0:
                        continue

                    # We select the first filtered proof to include in the presentation
                    vc["proof"] = proofs[0]

                    # We append our credential matching the requested query
                    presentation["verifiableCredential"].append(vc)

                    # We move onto the next query
                    continue

        # We sign the presentation
        agent.set_token(
            agent.request_token(self.wallet_id, wallet.get("wallet_key")).get("token")
        )
        vp = agent.sign_presentation(presentation, proof_options).get(
            "verifiablePresentation"
        )

        # We send the verifiable presentation to the exchange endpoint
        _log(f"POST {self.exchange_url} (submit verifiablePresentation)")
        r = requests.post(self.exchange_url, json={"verifiablePresentation": vp})

        # If the response fails, we abandon the exchange
        if r.status_code != 200:
            _log(f"Presentation submit failed: HTTP {r.status_code} {r.text[:500]}", logging.ERROR)
            return

        _log(f"Presentation submit: HTTP {r.status_code}")

        exchange_response = {}
        try:
            exchange_response = r.json()
            if isinstance(exchange_response, dict):
                _log(f"Exchange follow-up keys: {list(exchange_response.keys())}")
        except ValueError:
            pass

        # We store an event notification of the presentation exchange
        notification = Notification(
            id=str(uuid.uuid4()),
            type="vcapi_exchange",
            title="Presentation Sent",
            origin=vpr.get("domain") or self.exchange_url,
            message=reason or "Presentation sent",
            timestamp=str(datetime.now().isoformat()),
        ).model_dump()
        await self.askar.append("notifications", notification)
        _log("Presentation exchange notification stored")
        return exchange_response
