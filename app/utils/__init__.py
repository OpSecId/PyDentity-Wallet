"""
Utility functions for PyDentity Wallet
"""
from datetime import datetime, timezone
from typing import Dict, Any
import queue
import threading

from .device import is_mobile, get_device_type
from .coercion import as_list


# Global event broadcaster for real-time notifications
class NotificationBroadcaster:
    """Simple in-memory event broadcaster for SSE notifications"""
    
    def __init__(self):
        self.listeners = {}
        self.lock = threading.Lock()
    
    def subscribe(self, wallet_id: str) -> queue.Queue:
        """Subscribe to notifications for a specific wallet"""
        q = queue.Queue(maxsize=10)
        with self.lock:
            if wallet_id not in self.listeners:
                self.listeners[wallet_id] = []
            self.listeners[wallet_id].append(q)
        return q
    
    def unsubscribe(self, wallet_id: str, q: queue.Queue):
        """Unsubscribe from notifications"""
        with self.lock:
            if wallet_id in self.listeners:
                try:
                    self.listeners[wallet_id].remove(q)
                    if not self.listeners[wallet_id]:
                        del self.listeners[wallet_id]
                except ValueError:
                    pass
    
    def broadcast(self, wallet_id: str, event_type: str, data: Dict[str, Any]):
        """Broadcast an event to all listeners for a wallet"""
        with self.lock:
            if wallet_id in self.listeners:
                dead_queues = []
                for q in self.listeners[wallet_id]:
                    try:
                        q.put_nowait({
                            'type': event_type,
                            'data': data,
                            'timestamp': datetime.now(timezone.utc).isoformat()
                        })
                    except queue.Full:
                        dead_queues.append(q)
                
                # Remove full/dead queues
                for q in dead_queues:
                    try:
                        self.listeners[wallet_id].remove(q)
                    except ValueError:
                        pass


# Global broadcaster instance
notification_broadcaster = NotificationBroadcaster()


# Notification Management Functions
async def create_notification(wallet_id: str, notification_id: str, notification_type: str, title: str, details: dict):
    """
    Create a new notification using Askar profiles for user isolation.
    
    Args:
        wallet_id: Wallet ID (used as profile name)
        notification_id: Unique notification ID (usually exchange_id)
        notification_type: Type of notification (cred_offer, pres_request, etc.)
        title: Notification title
        details: Notification details dict
    """
    from app.plugins import AskarStorage
    from datetime import datetime, timezone
    from flask import current_app
    
    askar = AskarStorage.for_wallet(wallet_id)
    
    notification = {
        'id': notification_id,
        'new': True,
        'type': notification_type,
        'title': title,
        'details': details,
        'created_at': datetime.now(timezone.utc).isoformat()
    }
    
    # Store in wallet's profile
    await askar.store(
        category='notifications',
        key=notification_id,
        data=notification,
        tags={'type': notification_type}
    )
    
    current_app.logger.info(f"✅ Created notification in profile {wallet_id}: {notification_id}")
    
    return notification


async def delete_notification(wallet_id: str, notification_id: str):
    """
    Delete a specific notification from wallet's profile.
    
    Args:
        wallet_id: Wallet ID (profile name)
        notification_id: Unique notification ID to delete
    """
    from app.plugins import AskarStorage
    from flask import current_app
    
    askar = AskarStorage.for_wallet(wallet_id)
    
    try:
        deleted = await askar.delete(
            category='notifications',
            key=notification_id
        )
        if deleted:
            current_app.logger.info(f"✅ Deleted notification from profile {wallet_id}: {notification_id}")
        return deleted
    except Exception as e:
        current_app.logger.warning(f"Failed to delete notification {notification_id}: {e}")
        return False


async def get_notifications(wallet_id: str) -> list:
    """
    Get all notifications for a wallet from its Askar profile.
    
    Args:
        wallet_id: Wallet ID (profile name)
    
    Returns:
        List of notifications sorted by created_at (newest first)
    """
    from app.plugins import AskarStorage
    from flask import current_app
    
    askar = AskarStorage.for_wallet(wallet_id)
    
    # Fetch all notifications from the wallet's profile
    notifications = await askar.fetch_all_by_tag(
        category='notifications',
        tags={}  # Empty tags to fetch all
    )
    
    current_app.logger.info(f"📋 Fetched {len(notifications)} notifications from profile {wallet_id}")
    
    # Sort by created_at (newest first)
    if notifications:
        notifications.sort(key=lambda n: n.get('created_at', ''), reverse=True)
    
    return notifications or []


def beautify_anoncreds(
    attributes: Dict[str, Any],
    schema_id: str = None,
    schema_name: str = None,
    schema_version: str = None,
    cred_def_id: str = None,
    cred_def_tag: str = None,
    issuer_id: str = None,
    connection_label: str = None,
    issuer_image: str = None,
    created_at: str = None,
    cred_ex_id: str = None
) -> tuple[Dict[str, Any], Dict[str, str]]:
    """
    Transform AnonCreds credential data into W3C Verifiable Credential format.
    
    AnonCreds to W3C VC Field Mappings:
        credDefTag -> name (credential name)
        issuerId -> issuer.id
        connectionLabel -> issuer.name
        schemaId -> credentialSchema.id
        schemaName -> credentialSchema.name
        schemaVersion -> credentialSchema.description
        credDefId -> proof.verificationMethod
    
    Args:
        attributes: Dictionary of credential attributes (name: value pairs)
        schema_id: AnonCreds schema ID
        schema_name: Human-readable schema name
        schema_version: Schema version
        cred_def_id: AnonCreds credential definition ID
        cred_def_tag: Credential definition tag (used as credential name)
        issuer_id: Issuer DID
        connection_label: Connection label (used as issuer name)
        issuer_image: URL to issuer logo/image (optional)
        created_at: ISO 8601 timestamp of credential issuance
        cred_ex_id: Credential exchange ID (optional)
    
    Returns:
        Tuple of (credential_dict, tags_dict):
            - credential_dict: W3C VC in dict format
            - tags_dict: Metadata tags for Askar storage (CredentialTags)
    """
    from app.models.credential import Credential, IssuerInfo, CredentialSchema
    
    # Extract issuer DID from cred_def_id if not provided directly
    if not issuer_id and cred_def_id:
        # Split on '/' and take the first part as issuer ID
        issuer_id = cred_def_id.split('/')[0]
    
    # Use connection_label as issuer name, fallback to "Unknown Issuer"
    issuer_name = connection_label or "Unknown Issuer"
    
    # Use cred_def_tag as credential name, fallback to schema_name
    credential_name = cred_def_tag or schema_name or "Credential"
    
    # Use current timestamp if not provided
    if not created_at:
        created_at = datetime.now(timezone.utc).isoformat()
    
    # Build credential schema if schema_id provided
    cred_schema = None
    if schema_id:
        cred_schema = CredentialSchema(
            id=schema_id,
            type="AnonCredsSchema",
            name=schema_name,
            version=schema_version,
            description=schema_version  # Map schemaVersion to description as requested
        )
    
    # Build proof with cred_def_id as verificationMethod
    proof = None
    if cred_def_id:
        proof = {
            "type": "DataIntegrityProof",
            "cryptosuite": "vc-di-ac-2025",
            "created": created_at,
            "verificationMethod": cred_def_id,
            "proofPurpose": "assertionMethod"
        }
    
    # Build W3C Verifiable Credential using Credential model (clean, no metadata)
    credential = Credential(
        context=[
            "https://www.w3.org/ns/credentials/v2",
            "https://www.w3.org/ns/credentials/undefined-terms/v2"
        ],
        type=["VerifiableCredential", (schema_name or credential_name).replace(" ", "")],
        id=f"urn:uuid:{cred_ex_id}" if cred_ex_id else None,
        name=credential_name,  # credDefTag -> name
        issuer=IssuerInfo(
            id=issuer_id or "did:unknown",  # issuerId -> issuer.id
            name=issuer_name,  # connectionLabel -> issuer.name
            image=issuer_image if issuer_image else None
        ),
        validFrom=created_at,
        credentialSubject=attributes,
        credentialSchema=cred_schema,  # schemaId, schemaName, schemaVersion mapped
        proof=proof  # credDefId -> proof.verificationMethod
    )
    
    # Build tags for Askar storage (metadata)
    from app.plugins.askar import CredentialTags
    
    received_at = datetime.now(timezone.utc).isoformat()
    tags = CredentialTags(
        schema_id=schema_id,
        schema_name=schema_name,
        schema_version=schema_version,
        cred_def_id=cred_def_id,
        cred_def_tag=cred_def_tag,
        cred_ex_id=cred_ex_id,
        issuer_id=issuer_id or "did:unknown",
        issuer_name=issuer_name,
        credential_name=credential_name,
        received_at=received_at
    )
    
    # Remove None values from tags
    tags = {k: v for k, v in tags.items() if v is not None}
    
    # Return both credential and tags
    return credential.model_dump(), tags


__all__ = [
    'is_mobile',
    'get_device_type',
    'as_list',
    'notification_broadcaster',
    'create_notification',
    'delete_notification',
    'get_notifications',
    'beautify_anoncreds'
]


