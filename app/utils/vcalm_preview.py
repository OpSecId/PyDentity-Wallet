"""Human-readable summaries for VCALM consent prompts."""

from __future__ import annotations

from typing import Any

from app.utils.coercion import as_list


def _issuer_label(issuer: Any) -> str:
    if isinstance(issuer, str):
        return issuer
    if isinstance(issuer, dict):
        return issuer.get("name") or issuer.get("id") or "Unknown issuer"
    return "Unknown issuer"


def _issuer_id(issuer: Any) -> str:
    if isinstance(issuer, str):
        return issuer
    if isinstance(issuer, dict):
        return issuer.get("id") or ""
    return ""


def _credential_name(vc: dict) -> str:
    types = [t for t in vc.get("type", []) if t != "VerifiableCredential"]
    if types:
        return types[-1]
    return "Credential"


def _subject_fields(vc: dict) -> dict[str, Any]:
    subject = vc.get("credentialSubject", {})
    if not isinstance(subject, dict):
        return {}
    skip = {"id", "type"}
    return {k: v for k, v in subject.items() if k not in skip and v is not None}


def _match_credential_for_query(credentials: list[dict], cred_query: dict) -> dict | None:
    example = cred_query.get("example")
    if not isinstance(example, dict):
        return None
    example_types = example.get("type")
    example_context = example.get("@context")
    if not example_types or not example_context:
        return None

    for credential in credentials:
        if not isinstance(credential, dict):
            continue
        if set(example_types).issubset(credential.get("type", [])) and set(
            example_context
        ).issubset(credential.get("@context", [])):
            return credential
    return None


def presentation_preview(
    vpr: dict,
    *,
    holder_id: str | None = None,
    credentials: list[dict] | None = None,
) -> dict[str, Any]:
    """Build UI preview for a verifiablePresentationRequest."""
    domain = vpr.get("domain") or ""
    query_types: list[str] = []
    shared_credentials: list[dict] = []
    reasons: list[str] = []

    for query in as_list(vpr.get("query"), label="query"):
        qtype = query.get("type", "")
        if qtype:
            query_types.append(qtype)

        if qtype == "DIDAuthentication":
            reason = query.get("reason")
            if reason:
                reasons.append(reason)

        if qtype == "QueryByExample" and credentials:
            for cred_query in as_list(
                query.get("credentialQuery"), label="credentialQuery"
            ):
                if not cred_query.get("required", True):
                    continue
                reason = cred_query.get("reason")
                if reason:
                    reasons.append(reason)
                matched = _match_credential_for_query(credentials, cred_query)
                if matched:
                    shared_credentials.append(
                        {
                            "name": _credential_name(matched),
                            "issuer": _issuer_label(matched.get("issuer")),
                            "attributes": _subject_fields(matched),
                        }
                    )

    has_auth = "DIDAuthentication" in query_types
    has_share = "QueryByExample" in query_types

    if has_auth and has_share:
        title = f"Sign in and share with {domain}?" if domain else "Sign in and share?"
        confirm_label = "Continue"
        decline_label = "Cancel"
    elif has_share:
        title = f"Share credentials with {domain}?" if domain else "Share credentials?"
        confirm_label = "Share"
        decline_label = "Don't share"
    else:
        title = f"Sign in to {domain}?" if domain else "Sign in?"
        confirm_label = "Sign in"
        decline_label = "Cancel"

    detail = (
        f"{domain} wants to verify your identity."
        if has_auth and not has_share
        else f"{domain} is requesting information from your wallet."
        if domain
        else "A site is requesting information from your wallet."
    )

    return {
        "consentType": "presentation",
        "title": title,
        "detail": detail,
        "domain": domain,
        "queryTypes": query_types,
        "holderDid": holder_id,
        "reasons": reasons,
        "sharedCredentials": shared_credentials,
        "confirmLabel": confirm_label,
        "declineLabel": decline_label,
    }


def credential_storage_preview(vp: dict) -> dict[str, Any]:
    """Build UI preview for credentials inside a verifiablePresentation."""
    credentials: list[dict] = []
    vcs = vp.get("verifiableCredential")
    if isinstance(vcs, dict):
        vcs = [vcs]
    elif not isinstance(vcs, list):
        vcs = []

    for vc in vcs:
        if not isinstance(vc, dict):
            continue
        credentials.append(
            {
                "name": _credential_name(vc),
                "issuer": _issuer_label(vc.get("issuer")),
                "issuerId": _issuer_id(vc.get("issuer")),
                "types": vc.get("type", []),
                "attributes": _subject_fields(vc),
            }
        )

    count = len(credentials)
    if count == 1:
        title = "Store this credential?"
        confirm_label = "Store"
    else:
        title = f"Store {count} credentials?" if count else "Store credential?"
        confirm_label = "Store all"

    return {
        "consentType": "store",
        "title": title,
        "detail": "Review the credential below before saving it to your wallet.",
        "credentials": credentials,
        "confirmLabel": confirm_label,
        "declineLabel": "Don't store",
    }
