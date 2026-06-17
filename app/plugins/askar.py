from aries_askar import Store, AskarError
import hashlib
import logging
import json
from typing import TypedDict, Optional, List
from config import Config

logger = logging.getLogger(__name__)


class ProfileTags(TypedDict, total=False):
    """Tags for profile storage (client_id -> wallet_id mapping)"""
    pass  # No tags needed for profiles


class WalletTags(TypedDict, total=False):
    """Tags for wallet storage"""
    did: List[str]  # List of DIDs associated with wallet


class CredentialTags(TypedDict, total=False):
    """Tags for credential storage (metadata not stored in W3C VC)"""
    schema_id: str              # AnonCreds schema ID
    schema_name: str            # Schema name for display/filtering
    schema_version: str         # Schema version
    cred_def_id: str            # AnonCreds credential definition ID
    cred_def_tag: str           # Credential definition tag
    cred_ex_id: str             # Credential exchange ID
    issuer_id: str              # Issuer DID
    issuer_name: str            # Issuer name (connection label)
    credential_name: str        # Credential name (cred_def_tag or schema_name)
    received_at: str            # ISO 8601 timestamp when received


class ConnectionTags(TypedDict, total=False):
    """Tags for connection storage"""
    connection_id: str
    their_did: str
    their_label: str
    label: str
    state: str


class NotificationTags(TypedDict, total=False):
    """Tags for notification storage"""
    type: str  # e.g., 'cred_offer', 'pres_request'
    exchange_id: str


class MessageTags(TypedDict, total=False):
    """Tags for message storage"""
    connection_id: str
    sent_time: str


class ExchangeTags(TypedDict, total=False):
    """Tags for exchange storage"""
    exchange_id: str
    state: str
    role: str


class AskarStorageKeys:
    # System-level keys (stored in 'global' profile)
    PROFILES = "profiles"  # Maps client_id -> wallet_id
    WEB_AUTHN_CREDENTIALS = "webauthn/credentials"
    
    # User-specific keys (stored in wallet_id profile)
    WALLETS = "wallets"  # Wallet metadata (tokens, keys)
    CREDENTIALS = "credentials"
    CONNECTIONS = "connections"
    NOTIFICATIONS = "notifications"
    MESSAGES = "messages"
    EXCHANGES = "exchanges"
    CRED_OFFERS = "cred_offers"
    PRES_REQUESTS = "pres_requests"
    
    # Legacy/deprecated
    TOKENS = "tokens"
    SECRETS = "secrets"
    SETTINGS = "settings"
    VCALM_EXCHANGES = "vcalm/exchanges"
    
    # Mapping of categories to their tag models
    TAG_MODELS = {
        PROFILES: ProfileTags,
        WALLETS: WalletTags,
        CREDENTIALS: CredentialTags,
        CONNECTIONS: ConnectionTags,
        NOTIFICATIONS: NotificationTags,
        MESSAGES: MessageTags,
        EXCHANGES: ExchangeTags,
    }

async def initialize_askar_storage():
    """Provision the Askar store and ensure the global profile exists."""
    store = AskarStorage.global_store()
    await store.provision(recreate=False)
    await store.create_profile(AskarStorage.GLOBAL_PROFILE)


class AskarStorage:
    # Profile name for system-level data
    GLOBAL_PROFILE = "global"
    
    def __init__(self, profile: str = None):
        """
        Initialize Askar storage with a specific profile.
        
        Args:
            profile: Profile name to use. Defaults to GLOBAL_PROFILE if not specified.
                    - "global" for system-level data (profiles, webauthn)
                    - wallet_id for wallet-specific data (credentials, connections, etc.)
        """
        self.db = Config.ASKAR_DB
        self.key = Store.generate_raw_key(
            hashlib.md5(Config.SECRET_KEY.encode()).hexdigest()
        )
        self.profile = profile or self.GLOBAL_PROFILE
    
    @classmethod
    def for_wallet(cls, wallet_id: str):
        """Factory method to create storage for a specific wallet."""
        return cls(profile=wallet_id)
    
    @classmethod
    def global_store(cls):
        """Factory method to create storage for global/system data."""
        return cls(profile=cls.GLOBAL_PROFILE)

    async def provision(self, recreate=False):
        """Provision the main Askar store"""
        logger.warning(self.db)
        await Store.provision(self.db, "raw", self.key, recreate=recreate)

    async def create_profile(self, profile_name: str = None):
        """
        Create a new profile in the store.
        
        Args:
            profile_name: Name of the profile to create. If None, uses self.profile
        """
        profile = profile_name or self.profile
        try:
            store = await Store.open(self.db, "raw", self.key)
            await store.create_profile(profile)
            logger.info(f"✅ Created profile: {profile}")
            return True
        except AskarError as e:
            # Profile might already exist
            logger.debug(f"Profile creation note for '{profile}': {e}")
            return False

    async def open(self):
        """
        Open the store with this instance's profile.
        
        Askar profiles provide separate keyspaces within a single store.
        Each wallet_id gets its own profile for complete data isolation.
        
        Note: Profiles must be created with create_profile() before first use.
        """
        return await Store.open(self.db, "raw", self.key, profile=self.profile)

    async def fetch(self, category: str, key: str = "data"):
        """
        Fetch data from this instance's profile.
        
        Args:
            category: Storage category (e.g., "credentials", "profiles")
            key: Storage key within category (default: "data" for arrays)
        
        Examples:
            # Global storage
            global_store = AskarStorage.global_store()
            profile = await global_store.fetch("profiles", client_id)
            
            # Wallet storage
            wallet_store = AskarStorage.for_wallet(wallet_id)
            credentials = await wallet_store.fetch("credentials")  # key defaults to "data"
        """
        try:
            store = await self.open()
            logger.info(f"🔍 Fetching from profile '{self.profile}': category={category}, key={key}")
            async with store.session() as session:
                entry = await session.fetch(category, key)
            result = json.loads(entry.value) if entry else None
            logger.info(f"{'✅ Found' if result else '❌ Not found'}")
            return result
        except (AskarError, ValueError, KeyError) as e:
            logger.error(f"❌ Fetch failed in profile '{self.profile}': {e}")
            return None

    async def fetch_name_by_tag(self, category: str, tags: dict):
        """Fetch entry name by tag from this instance's profile"""
        try:
            store = await self.open()
            async with store.session() as session:
                entries = await session.fetch_all(category, tags, limit=1)
            if entries and len(entries) > 0:
                return entries[0].name
            return None
        except (AskarError, IndexError, AttributeError):
            return None

    async def fetch_entry_by_tag(self, category: str, tags: dict):
        """Fetch entry by tag from this instance's profile"""
        try:
            store = await self.open()
            async with store.session() as session:
                entries = await session.fetch_all(category, tags, limit=1)
            if entries and len(entries) > 0:
                return json.loads(entries[0].value)
            return None
        except (AskarError, ValueError, IndexError, AttributeError):
            return None

    async def store(self, category: str, key: str, data: dict, tags: Optional[dict] = None):
        """
        Store data in this instance's profile.
        
        Args:
            category: Storage category (use AskarStorageKeys constants)
            key: Storage key within category
            data: Data to store
            tags: Optional tags for indexing (see TAG_MODELS for category-specific tags)
        
        Examples:
            # Store wallet with DID tags
            wallet_store = AskarStorage.for_wallet(wallet_id)
            await wallet_store.store(
                AskarStorageKeys.WALLETS,
                "data",
                wallet_data,
                WalletTags(did=[did])
            )
            
            # Store notification with type tag
            await wallet_store.store(
                AskarStorageKeys.NOTIFICATIONS,
                notification_id,
                notification_data,
                NotificationTags(type='cred_offer')
            )
        """
        try:
            store = await self.open()
            logger.info(f"📝 Storing in profile '{self.profile}': category={category}, key={key}")
            async with store.session() as session:
                await session.insert(category, key, json.dumps(data), tags)
            logger.info(f"✅ Stored successfully")
            return True
        except AskarError as e:
            logger.error(f"❌ Store failed in profile '{self.profile}': {e}")
            return False

    async def append(self, category: str, data: dict, key: str = "data", tags: dict = None):
        """
        Append to an array in this instance's profile.
        
        Args:
            category: Storage category
            data: Data to append
            key: Storage key (default: "data")
            tags: Optional tags
        """
        try:
            store = await self.open()
            async with store.session() as session:
                entry = await session.fetch(category, key)
                if entry:
                    entries = json.loads(entry.value)
                else:
                    entries = []
                entries.append(data)
                await session.replace(category, key, json.dumps(entries), tags)
            return True
        except (AskarError, ValueError):
            return False

    async def update(self, category: str, key: str, data: dict, tags: dict = None):
        """
        Update/replace data in this instance's profile.
        
        Args:
            category: Storage category
            key: Storage key within category
            data: Data to store
            tags: Optional tags
        """
        try:
            store = await self.open()
            async with store.session() as session:
                await session.replace(category, key, json.dumps(data), tags)
            return True
        except AskarError:
            return False

    async def delete(self, category: str, key: str):
        """
        Delete an entry from this instance's profile.
        
        Args:
            category: Storage category
            key: Storage key within category
        """
        try:
            store = await self.open()
            async with store.session() as session:
                await session.remove(category, key)
            return True
        except AskarError:
            # Profile doesn't exist or key not found - consider it deleted
            return True

    async def fetch_all_by_tag(self, category: str, tags: dict):
        """
        Fetch all entries matching tags from this instance's profile.
        
        Args:
            category: Storage category
            tags: Tags to filter by
        """
        try:
            store = await self.open()
            async with store.session() as session:
                entries = await session.fetch_all(category, tags, limit=100)
                results = []
                if entries:
                    for entry in entries:
                        results.append(json.loads(entry.value))
                return results
        except (AskarError, ValueError, IndexError, AttributeError):
            return []
