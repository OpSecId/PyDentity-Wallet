from app.models.vcalm_exchange import VcalmExchange
from app.plugins.vcapi import exchange_to_client_response
from app.utils.vcalm_preview import credential_storage_preview, presentation_preview


def test_presentation_preview_did_authentication():
    vpr = {
        "domain": "example.com",
        "challenge": "abc",
        "query": {
            "type": "DIDAuthentication",
            "acceptedMethods": [{"method": "key"}],
        },
    }
    preview = presentation_preview(vpr, holder_id="did:key:z6Mkholder")
    assert preview["consentType"] == "presentation"
    assert preview["title"] == "Sign in to example.com?"
    assert preview["confirmLabel"] == "Sign in"
    assert preview["queryTypes"] == ["DIDAuthentication"]
    assert preview["holderDid"] == "did:key:z6Mkholder"


def test_presentation_preview_query_by_example():
    vpr = {
        "domain": "verifier.example",
        "query": {
            "type": "QueryByExample",
            "credentialQuery": {
                "required": True,
                "example": {
                    "type": ["MemberCard"],
                    "@context": ["https://www.w3.org/ns/credentials/v2"],
                },
            },
        },
    }
    credentials = [
        {
            "type": ["VerifiableCredential", "MemberCard"],
            "@context": ["https://www.w3.org/ns/credentials/v2"],
            "issuer": {"name": "Acme"},
            "credentialSubject": {"memberId": "42"},
        }
    ]
    preview = presentation_preview(vpr, credentials=credentials)
    assert preview["title"] == "Share credentials with verifier.example?"
    assert preview["confirmLabel"] == "Share"
    assert len(preview["sharedCredentials"]) == 1
    assert preview["sharedCredentials"][0]["name"] == "MemberCard"


def test_credential_storage_preview_single():
    vp = {
        "verifiableCredential": {
            "type": ["VerifiableCredential", "MemberCard"],
            "issuer": {"name": "Acme Corp", "id": "did:web:acme.example"},
            "credentialSubject": {"memberId": "99", "tier": "gold"},
        }
    }
    preview = credential_storage_preview(vp)
    assert preview["consentType"] == "store"
    assert preview["title"] == "Store this credential?"
    assert preview["credentials"][0]["issuer"] == "Acme Corp"
    assert preview["credentials"][0]["attributes"]["memberId"] == "99"


def test_exchange_to_client_response_awaiting_presentation():
    exchange = VcalmExchange(
        id="ex-1",
        wallet_id="wallet-1",
        exchange_url="https://rp.example/exchange",
        protocol="vcalm",
        status="awaiting_presentation_consent",
        created_at="2026-01-01T00:00:00+00:00",
        expires_at="2026-01-01T01:00:00+00:00",
        preview={"consentType": "presentation", "title": "Sign in?"},
    )
    result = exchange_to_client_response(exchange)
    assert result["status"] == "awaiting_consent"
    assert result["consentType"] == "presentation"
    assert result["exchangeId"] == "ex-1"
