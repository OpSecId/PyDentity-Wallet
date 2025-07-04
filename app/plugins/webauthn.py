import webauthn
import datetime
import json
import base64
from config import Config
from app.models.webauthn import WebAuthnCredential
from app.plugins import AskarStorage
from webauthn.helpers.structs import PublicKeyCredentialDescriptor  
from webauthn.helpers import base64url_to_bytes
from webauthn.helpers.structs import RegistrationCredential, AuthenticatorAttestationResponse, AuthenticationCredential, AuthenticatorAssertionResponse
    
askar = AskarStorage()

class WebAuthnProvider:
    def __init__(self):
        self.rp_id = Config.DOMAIN
        self.rp_name = Config.APP_NAME
        self.origin = Config.APP_URL
        self.challenge_exp = 10 # Challenge expiration minutes

    async def prepare_credential_creation(self, client_id, username):
        public_credential_creation_options = webauthn.generate_registration_options(
            rp_id=self.rp_id,
            rp_name=self.rp_name,
            user_id=client_id.encode(),
            user_name=username,
        )
        Config.REGISTRATION_CHALLENGES.set(client_id, public_credential_creation_options.challenge)
        if Config.SESSION_TYPE == 'redis':
            Config.REGISTRATION_CHALLENGES.expire(client_id, datetime.timedelta(minutes=self.challenge_exp))

        return json.loads(webauthn.options_to_json(public_credential_creation_options))

    async def create_registration_credential(self, data):
        registration_credential = RegistrationCredential(
            id=data.get('id'),
            raw_id=base64url_to_bytes(data.get('rawId')),
            response=AuthenticatorAttestationResponse(
                client_data_json=base64.urlsafe_b64decode(data['response']['clientDataJSON']+'==='),
                attestation_object=base64.urlsafe_b64decode(data['response']['attestationObject']+'==='),
                transports=data['response']['transports']
            ),
            type='public-key'
        )
        return registration_credential
        
    async def verify_and_save_credential(self, client_id, registration_credential):
        """Verify that a new credential is valid for the """
        expected_challenge = Config.REGISTRATION_CHALLENGES.get(client_id)
        
        # If the credential is somehow invalid (i.e. the challenge is wrong),
        # this will raise an exception. It's easier to handle that in the view
        # since we can send back an error message directly.
        auth_verification = webauthn.verify_registration_response(
            credential=registration_credential,
            expected_challenge=expected_challenge,
            expected_origin=self.origin,
            expected_rp_id=self.rp_id,
        )

        # At this point verification has succeeded and we can save the credential
        credential = WebAuthnCredential(
            client_id=client_id,
            credential_id=auth_verification.credential_id.hex(),
            credential_public_key=auth_verification.credential_public_key.hex(),
            current_sign_count=0
        ).model_dump()
        tags = {
            'client_id': client_id,
        }
        await askar.store('webauthn/credential', credential['credential_id'], credential, tags) 

    async def prepare_login_with_credential(self, client_id):
        """
        Prepare the authentication options for a user trying to log in.
        """
        credential_id = await askar.fetch_name_by_tag(
            'webauthn/credential', {'client_id': client_id})
        # return user_credentials
        allowed_credentials = [
            PublicKeyCredentialDescriptor(id=bytes.fromhex(credential_id))
            # for credential in user.credentials
        ]

        authentication_options = webauthn.generate_authentication_options(
            rp_id=self.rp_id,
            allow_credentials=allowed_credentials,
        )

        Config.AUTHENTICATION_CHALLENGES.set(client_id, authentication_options.challenge)
        Config.AUTHENTICATION_CHALLENGES.expire(client_id, datetime.timedelta(minutes=self.challenge_exp))

        return json.loads(webauthn.options_to_json(authentication_options))

    async def verify_authentication_credential(self, client_id, attestation):
        """
        Verify a submitted credential against a credential in the database and the
        challenge stored in redis.
        """
        authentication_credential = AuthenticationCredential(
            id=attestation['id'],
            raw_id=base64.urlsafe_b64decode(attestation['rawId']+'==='),
            response=AuthenticatorAssertionResponse(
                authenticator_data=base64.urlsafe_b64decode(attestation['response']['authenticatorData']+'==='),
                client_data_json=base64.urlsafe_b64decode(attestation['response']['clientDataJSON']+'==='),
                signature=base64.urlsafe_b64decode(attestation['response']['signature']+'===')
            ),
            type='public-key',
        )
        expected_challenge = Config.AUTHENTICATION_CHALLENGES.get(client_id)
        credential_id = await askar.fetch_name_by_tag(
            'webauthn/credential', {'client_id': client_id})
        credential = await askar.fetch('webauthn/credential', credential_id)
        
        # This will raise if the credential does not authenticate
        # It seems that safari doesn't track credential sign count correctly, so we just
        # have to leave it on zero so that it will authenticate
        webauthn.verify_authentication_response(
            credential=authentication_credential,
            expected_challenge=expected_challenge,
            expected_origin=self.origin,
            expected_rp_id=self.rp_id,
            credential_public_key=bytes.fromhex(credential['credential_public_key']),
            credential_current_sign_count=credential['current_sign_count']
        )
        
        # After a successful authentication, expire the challenge so it can't be used again.
        Config.AUTHENTICATION_CHALLENGES.expire(client_id, datetime.timedelta(seconds=1))

        
        # Update the credential sign count after using, then save it back to the database.
        # This is mainly for reference since we can't use it because of Safari's weirdness.
        credential['current_sign_count'] += 1
        # session['webauthn_credential'] = stored_credential.model_dump()
        tags = {'client_id': client_id}
        await askar.update('webauthn/credential', credential_id, credential, tags)