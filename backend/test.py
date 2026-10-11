
from pathlib import Path
from dotenv import load_dotenv
from composio import Composio, SESSION_PRESET_DIRECT_TOOLS
import os

load_dotenv(Path(__file__).resolve().parent / ".env")

if not os.getenv("COMPOSIO_API_KEY"):
    raise RuntimeError("COMPOSIO_API_KEY missing")

composio = Composio()

user_id = "pg-test-cd1a5215-b12b-41c6-90fb-f944fa9d828e"
slack_account_id = "ca_iZ_BXAhPV7Al"

session = composio.sessions.create(
    user_id=user_id,
    toolkits=["slack"],
    tools={
        "slack": {

            "enable": ["SLACK_SEND_MESSAGE"]
        }
    },
    connected_accounts={
        "slack": [slack_account_id]
    },
    session_preset=SESSION_PRESET_DIRECT_TOOLS,
    sandbox={"enable": False},
    mcp=True,
)

print("Restricted session created")

for toolkit in session.toolkits().items:
    if toolkit.name.lower() == "slack":
        connection = toolkit.connection
        print(
            "Slack active:",
            bool(connection and connection.is_active)
        )

print("Headers : ", session.mcp.headers["x-api-key"])

print("MCP URL : ", session.mcp.url)
print(
    "x-api-key header present:",
    "x-api-key" in session.mcp.headers
)
