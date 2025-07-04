from flask import current_app, session
from app.plugins import AgentController, AskarStorage
import secrets

askar = AskarStorage()
agent = AgentController()

async def provision_wallet(client_id):
    wallet_key = str(secrets.token_hex(16))
    
    wallet = agent.create_subwallet(client_id, wallet_key)
    wallet["wallet_key"] = wallet_key
    
    agent.set_token(wallet["token"])
    did = agent.create_did().get("result").get("did")
    
    with wallet["wallet_id"] as wallet_id:
        await askar.store('wallet', wallet_id, wallet, {'did': [did]})
        await askar.store('connections', wallet_id, [])
        await askar.store('credentials', wallet_id, [])
        await askar.store('notifications', wallet_id, [])
    
    return wallet

async def sync_session(wallet_id):
    # current_app.logger.warning(f"Session Sync: {client_id}")

    session["credentials"] = await askar.fetch("credentials", wallet_id)
    session["connections"] = await askar.fetch("connections", wallet_id)
    session["notifications"] = await askar.fetch("notifications", wallet_id)
    
async def sync_wallet(wallet_id):
    current_app.logger.warning(f"Synchronising Wallet: {wallet_id}")

    # Refresh token
    await agent.set_agent_auth(wallet_id)

    # Update Connections
    connections = []
    connections.extend(agent.get_connections().get("results"))
    await askar.update("connections", wallet_id, connections)

    # Update Credentials
    credentials = []
    credentials.extend(
        credential.get("cred_value")
        for credential in agent.get_w3c_credentials().get("results")
        if credential not in credentials
    )
    await askar.update("credentials", wallet_id, credentials)