import copy
import requests
import uuid
import logging
from datetime import datetime, timezone

from flask import current_app

from app.models.vcalm_exchange import VcalmExchange
from app.plugins.acapy import AgentController
from app.plugins.askar import AskarStorage, AskarStorageKeys
from app.plugins.vcalm_exchange_store import VcalmExchangeStore
from app.models.notification import Notification
from app.utils import as_list, create_notification
from app.utils.vcalm_preview import credential_storage_preview, presentation_preview

MAX_EXCHANGE_STEPS = 10

agent = AgentController()
logger = logging.getLogger(__name__)


def _log(message: str, level: int = logging.INFO) -> None:
    try:
        current_app.logger.log(level, message)
    except RuntimeError:
        logger.log(level, message)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class VcApiExchanger:
    def __init__(self, wallet_id=None, exchange_url=None, protocol: str = "vcapi"):
        self.wallet_id = wallet_id
        self.exchange_url = exchange_url
        self.protocol = protocol
        self.askar = (
            AskarStorage.for_wallet(wallet_id)
            if wallet_id
            else AskarStorage.global_store()
        )
        self.store = VcalmExchangeStore(wallet_id) if wallet_id else None
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

    async def start_exchange(self, exchange_id: str) -> VcalmExchange:
        exchange_state = VcalmExchange(
            id=exchange_id,
            wallet_id=self.wallet_id,
            exchange_url=self.exchange_url,
            protocol=self.protocol,
            status="processing",
            created_at=_utc_now(),
            expires_at=VcalmExchangeStore.new_expiry(),
        )
        message = self.initiate_exchange()
        return await self._advance(exchange_state, message)

    async def confirm_presentation(self, exchange_state: VcalmExchange) -> VcalmExchange:
        if not exchange_state.pending_vpr:
            exchange_state.status = "error"
            exchange_state.error_message = "No presentation request pending"
            return exchange_state

        exchange_state.status = "processing"
        vpr = exchange_state.pending_vpr
        exchange_state.pending_vpr = None
        exchange_state.presented = True
        exchange_state.step += 1

        follow_up = await self.present_credential(vpr)
        if not isinstance(follow_up, dict):
            exchange_state.status = "error"
            exchange_state.error_message = "Presentation failed"
            return exchange_state

        return await self._advance(exchange_state, follow_up)

    async def confirm_store(self, exchange_state: VcalmExchange) -> VcalmExchange:
        if not exchange_state.pending_vp:
            exchange_state.status = "error"
            exchange_state.error_message = "No credential pending storage"
            return exchange_state

        exchange_state.status = "processing"
        vp = exchange_state.pending_vp
        context = exchange_state.context_message or {}
        exchange_state.pending_vp = None
        exchange_state.context_message = None
        exchange_state.step += 1

        exchange_state.stored_credentials += await self.store_credential(vp)

        if context.get("verifiablePresentationRequest"):
            return await self._pause_for_presentation(
                exchange_state, context["verifiablePresentationRequest"]
            )

        if context.get("redirectUrl"):
            return self._complete(
                exchange_state,
                {
                    "status": "redirect",
                    "redirectUrl": context["redirectUrl"],
                    "presented": exchange_state.presented,
                    "storedCredentials": exchange_state.stored_credentials,
                },
            )

        return self._complete(
            exchange_state,
            {
                "status": "complete",
                "presented": exchange_state.presented,
                "storedCredentials": exchange_state.stored_credentials,
            },
        )

    async def skip_store(self, exchange_state: VcalmExchange) -> VcalmExchange:
        context = exchange_state.context_message or {}
        exchange_state.pending_vp = None
        exchange_state.context_message = None
        exchange_state.step += 1

        if context.get("verifiablePresentationRequest"):
            return await self._pause_for_presentation(
                exchange_state, context["verifiablePresentationRequest"]
            )

        if context.get("redirectUrl"):
            return self._complete(
                exchange_state,
                {
                    "status": "redirect",
                    "redirectUrl": context["redirectUrl"],
                    "presented": exchange_state.presented,
                    "storedCredentials": exchange_state.stored_credentials,
                },
            )

        return self._complete(
            exchange_state,
            {
                "status": "complete",
                "presented": exchange_state.presented,
                "storedCredentials": exchange_state.stored_credentials,
            },
        )

    async def abandon(self, exchange_state: VcalmExchange) -> VcalmExchange:
        exchange_state.status = "abandoned"
        exchange_state.pending_vpr = None
        exchange_state.pending_vp = None
        exchange_state.context_message = None
        exchange_state.result = {
            "status": "abandoned",
            "presented": exchange_state.presented,
            "storedCredentials": exchange_state.stored_credentials,
        }
        return exchange_state

    async def _advance(
        self, exchange_state: VcalmExchange, message: dict, *, depth: int = 0
    ) -> VcalmExchange:
        if depth >= MAX_EXCHANGE_STEPS or not isinstance(message, dict):
            return self._complete(
                exchange_state,
                {
                    "status": "complete",
                    "presented": exchange_state.presented,
                    "storedCredentials": exchange_state.stored_credentials,
                },
            )

        if message.get("verifiablePresentationRequest"):
            return await self._pause_for_presentation(
                exchange_state, message["verifiablePresentationRequest"]
            )

        if message.get("verifiablePresentation"):
            return await self._pause_for_store(exchange_state, message)

        if message.get("redirectUrl"):
            return self._complete(
                exchange_state,
                {
                    "status": "redirect",
                    "redirectUrl": message["redirectUrl"],
                    "presented": exchange_state.presented,
                    "storedCredentials": exchange_state.stored_credentials,
                },
            )

        return self._complete(
            exchange_state,
            {
                "status": "complete",
                "presented": exchange_state.presented,
                "storedCredentials": exchange_state.stored_credentials,
            },
        )

    async def _pause_for_presentation(
        self, exchange_state: VcalmExchange, vpr: dict
    ) -> VcalmExchange:
        wallet = await self.askar.fetch(AskarStorageKeys.WALLETS) or {}
        credentials = await self.askar.fetch(AskarStorageKeys.CREDENTIALS) or []
        exchange_state.status = "awaiting_presentation_consent"
        exchange_state.pending_vpr = vpr
        exchange_state.preview = presentation_preview(
            vpr,
            holder_id=wallet.get("holder_id"),
            credentials=credentials,
        )
        await self.store.persist(exchange_state)
        return exchange_state

    async def _pause_for_store(
        self, exchange_state: VcalmExchange, message: dict
    ) -> VcalmExchange:
        exchange_state.status = "awaiting_store_consent"
        exchange_state.pending_vp = message["verifiablePresentation"]
        exchange_state.context_message = message
        exchange_state.preview = credential_storage_preview(
            message["verifiablePresentation"]
        )
        await self.store.persist(exchange_state)
        return exchange_state

    def _complete(self, exchange_state: VcalmExchange, result: dict) -> VcalmExchange:
        exchange_state.status = "complete"
        exchange_state.result = result
        exchange_state.pending_vpr = None
        exchange_state.pending_vp = None
        exchange_state.context_message = None
        return exchange_state

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

    async def present_credential(self, vpr):
        _log("Building presentation for VCALM exchange request")
        wallet = await self.askar.fetch(AskarStorageKeys.WALLETS)
        if not wallet:
            _log("Wallet record not found for presentation", logging.ERROR)
            return None

        presentation, proof_options, reason = await self._build_presentation(
            vpr, wallet, copy.deepcopy(await self.askar.fetch(AskarStorageKeys.CREDENTIALS) or [])
        )
        if presentation is None:
            return None

        agent.set_token(
            agent.request_token(self.wallet_id, wallet.get("wallet_key")).get("token")
        )
        vp = agent.sign_presentation(presentation, proof_options).get(
            "verifiablePresentation"
        )

        _log(f"POST {self.exchange_url} (submit verifiablePresentation)")
        r = requests.post(self.exchange_url, json={"verifiablePresentation": vp})

        if r.status_code != 200:
            _log(
                f"Presentation submit failed: HTTP {r.status_code} {r.text[:500]}",
                logging.ERROR,
            )
            return None

        _log(f"Presentation submit: HTTP {r.status_code}")

        exchange_response = {}
        try:
            exchange_response = r.json()
            if isinstance(exchange_response, dict):
                _log(f"Exchange follow-up keys: {list(exchange_response.keys())}")
        except ValueError:
            pass

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

    async def _build_presentation(self, vpr, wallet, credentials):
        presentation = {
            "@context": ["https://www.w3.org/ns/credentials/v2"],
            "type": ["VerifiablePresentation"],
        }
        proof_options = {
            "proofType": "Ed25519Signature2020",
            "domain": vpr.get("domain"),
            "challenge": vpr.get("challenge"),
            "proofPurpose": "authentication",
        }
        reason = None

        for query in as_list(vpr.get("query"), label="verifiablePresentationRequest.query"):
            if query.get("type") == "DIDAuthentication":
                _log("VPR query: DIDAuthentication")
                reason = reason or "Signed in with DID Authentication"
                methods = [
                    method.get("method")
                    for method in as_list(
                        query.get("acceptedMethods"), label="acceptedMethods"
                    )
                ]
                if "key" not in methods:
                    return None, None, None

                presentation["holder"] = wallet["holder_id"]
                multikey = presentation["holder"].split(":")[-1]
                proof_options["verificationMethod"] = f"did:key:{multikey}#{multikey}"

            if query.get("type") == "QueryByExample":
                _log("VPR query: QueryByExample")
                if not presentation.get("verifiableCredential"):
                    presentation["verifiableCredential"] = []

                for cred_query in as_list(
                    query.get("credentialQuery"), label="credentialQuery"
                ):
                    if not cred_query.get("required", True):
                        continue

                    reason = cred_query.get("reason") or reason
                    example = cred_query.get("example")
                    accepted_cryptosuites = cred_query.get("acceptedCryptosuites")

                    vc = next(
                        (
                            credential
                            for credential in credentials
                            if isinstance(example, dict)
                            and set(example.get("type", [])).issubset(
                                credential.get("type", [])
                            )
                            and set(example.get("@context", [])).issubset(
                                credential.get("@context", [])
                            )
                        ),
                        None,
                    )
                    if vc is None:
                        _log("No matching credential for QueryByExample", logging.WARNING)
                        continue

                    vc = copy.deepcopy(vc)
                    proofs = vc.pop("proof", None)
                    proofs = proofs if isinstance(proofs, list) else [proofs]

                    filtered = []
                    for proof in proofs:
                        if not isinstance(proof, dict):
                            continue
                        if (
                            proof.get("type") == "Ed25519Signature2020"
                            and accepted_cryptosuites
                            and "Ed25519Signature2020" not in accepted_cryptosuites
                        ):
                            continue
                        if (
                            proof.get("type") == "DataIntegrityProof"
                            and accepted_cryptosuites
                            and proof.get("cryptosuite") not in accepted_cryptosuites
                        ):
                            continue
                        filtered.append(proof)

                    if not filtered:
                        continue

                    vc["proof"] = filtered[0]
                    presentation["verifiableCredential"].append(vc)

        return presentation, proof_options, reason


def exchange_to_client_response(exchange: VcalmExchange) -> dict:
    if exchange.status == "awaiting_presentation_consent":
        return {
            "status": "awaiting_consent",
            "consentType": "presentation",
            "exchangeId": exchange.id,
            "preview": exchange.preview,
            "presented": exchange.presented,
            "storedCredentials": exchange.stored_credentials,
        }
    if exchange.status == "awaiting_store_consent":
        return {
            "status": "awaiting_consent",
            "consentType": "store",
            "exchangeId": exchange.id,
            "preview": exchange.preview,
            "presented": exchange.presented,
            "storedCredentials": exchange.stored_credentials,
        }
    if exchange.status == "abandoned":
        return exchange.result or {"status": "abandoned"}
    if exchange.status == "error":
        return {
            "status": "error",
            "message": exchange.error_message or "Exchange failed",
            "presented": exchange.presented,
            "storedCredentials": exchange.stored_credentials,
        }
    return exchange.result or {
        "status": "complete",
        "presented": exchange.presented,
        "storedCredentials": exchange.stored_credentials,
    }
