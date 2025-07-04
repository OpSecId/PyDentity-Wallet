from flask import current_app, session
import uuid
from nanoid import generate
import httpx
from app.models.credentials import AnonCredsOffer, CredentialStatus, CredentialSchema
from app.services import AskarStorage, AgentController
from asyncio import run as await_
from config import Config

askar = AskarStorage()
agent = AgentController()

class WorkflowManager:
    def __init__(self):
        self.domain = Config.DOMAIN
        self.endpoint = Config.APP_URL
        
    def store_query(self, workflow_id, query):
        exchange_id = str(uuid.uuid4())
        service_endpoint = f"{self.endpoint}/workflows/{workflow_id}/exchanges/{exchange_id}"
        exchange = {
            "challenge": exchange_id,
            "domain": self.domain,
            "query": query,
            "interact": {
                "service": [
                    {
                        "type": "UnmediatedHttpPresentationService2021",
                        "serviceEndpoint": service_endpoint
                    }
                ]
            }
        }
        await_(askar.store(f'workflows/{workflow_id}', exchange_id, exchange))
        return service_endpoint
        
    def create_did_auth(self, methods=['key'], cryptosuites=['eddsa-jcs-2022']):
        query = {
            "type": "DIDAuthentication", 
            "acceptedMethods": [{'method': method} for method in methods],
            "acceptedCryptosuites": [{'cryptosuite': cryptosuite} for cryptosuite in cryptosuites],
        }
        exchange_url = self.store_query('auth', query)
        return exchange_url
    
    def vp_request_response(self, vp_request, holder):
        
        # Holder is a DID key
        multikey = holder.split(':')[-1]
        query = vp_request['query']
        cryptosuite = query['acceptedCryptosuites'][0]['cryptosuite']
        return {
            '@context': ['https://www.w3.org/ns/credentials/v2'],
            'type': ['VerifiablePresentation'],
            'holder': holder
        }, {
            'type': 'DataIntegrityProof',
            'domain': vp_request['domain'],
            'challenge': vp_request['challenge'],
            'cryptosuite': cryptosuite,
            'proofPurpose': 'authentication',
            'verificationMethod': f'did:key:{multikey}#{multikey}',
        }
        
    def exchange_handle(self, exchange_url):
        r = httpx.post(exchange_url, json={})
        exchange = r.json()
                
        if exchange.get('verifiablePresentation', None):
            vp = exchange['verifiablePresentation']
            for vc in vp['verifiableCredential']:
                if vc not in session['credentials']:
                    session['credentials'].append(vc)
            return
                    
        if exchange.get('verifiablePresentationRequest', None):
            vp_request = exchange['verifiablePresentationRequest']
            query = vp_request['query']
            
            requested_type = query[0]['credentialQuery']['example']['type']
            # requested_type = requested_type if isinstance(requested_type, str) else requested_type[0]

            # TODO, we need the holder did
            presentation, options = self.vp_request_response(vp_request)
            
            token = agent.request_token(session.get('client_id'))
            agent.set_token(token)
            vp = agent.sign_presentation(presentation, options).get('verifiablePresentation')
            
            httpx.post(exchange_url, json={'verifiablePresentation': vp})
            return
        return