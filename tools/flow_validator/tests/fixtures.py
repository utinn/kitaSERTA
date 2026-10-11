"""Sanitized synthetic Langflow exports shaped like a real Flow 3 export.

Chat Input -> Agent (tools from MCP Tools) -> Chat Output. No real
credentials, URLs, channel IDs or component code from any real flow.
"""

import copy
import json

MCP_CODE = '''from langflow.custom import Component
from langflow.io import MessageTextInput, Output


class MCPToolsComponent(Component):
    display_name = "MCP Tools"
    description = "Connect to an MCP server and expose its tools."
    inputs = [
        MessageTextInput(name="mcp_server", display_name="MCP Server"),
        MessageTextInput(name="tool", display_name="Tool"),
    ]
    outputs = [
        Output(display_name="Toolset", name="component_as_tool", method="build_tools"),
    ]

    async def build_tools(self):
        server = self.mcp_server
        if not server:
            raise ValueError("MCP server is not configured")
        tools = await self._load_tools(server)
        selected = [t for t in tools if not self.tool or t.name == self.tool]
        return selected

    async def _load_tools(self, server):
        client = self._client_for(server)
        try:
            return await client.list_tools()
        finally:
            await client.close()

    def _client_for(self, server):
        from langflow.base.mcp.util import MCPClient
        return MCPClient(server)
'''

AGENT_CODE = '''from langflow.base.agents.agent import LCToolsAgentComponent
from langflow.io import HandleInput, MessageInput, MultilineInput, Output


class AgentComponent(LCToolsAgentComponent):
    display_name = "Agent"
    inputs = [
        MessageInput(name="input_value", display_name="Input"),
        MultilineInput(name="system_prompt", display_name="Instructions"),
        HandleInput(name="tools", display_name="Tools", input_types=["Tool"], is_list=True),
    ]
    outputs = [Output(display_name="Response", name="response", method="message_response")]

    async def message_response(self):
        return await self.run_agent(self.build_agent())
'''

IO_CODE = '''from langflow.base.io.chat import ChatComponent


class {name}(ChatComponent):
    display_name = "{display}"

    def message(self):
        return self.input_value
'''


def field(input_type, ftype="str", value="", **extra):
    return {
        "_input_type": input_type, "type": ftype, "value": value,
        "required": False, "show": True, "advanced": False, **extra,
    }


def code_field(code):
    return {"type": "code", "value": code, "required": True, "show": True, "advanced": True, "dynamic": True}


def node(node_id, ctype, display, template, outputs):
    template = {"_type": "Component", **template}
    return {
        "id": node_id,
        "type": "genericNode",
        "position": {"x": 0, "y": 0},
        "data": {
            "id": node_id,
            "type": ctype,
            "node": {
                "display_name": display,
                "base_classes": sorted({t for _, types in outputs for t in types}),
                "template": template,
                "outputs": [
                    {"name": name, "types": types, "selected": types[0], "display_name": name}
                    for name, types in outputs
                ],
            },
        },
    }


def encode_handle(handle):
    return json.dumps(handle, separators=(",", ":")).replace('"', "œ")


def edge(source, source_type, output, out_types, target, field_name, in_types, ftype="str"):
    sh = {"dataType": source_type, "id": source, "name": output, "output_types": out_types}
    th = {"fieldName": field_name, "id": target, "inputTypes": in_types, "type": ftype}
    return {
        "id": f"xy-edge__{source}-{target}",
        "source": source,
        "target": target,
        "sourceHandle": encode_handle(sh),
        "targetHandle": encode_handle(th),
        "data": {"sourceHandle": sh, "targetHandle": th},
        "animated": False,
        "className": "",
        "selected": False,
    }


def reference_flow(ids=None, prompt="Notify the approved Slack channel from approved_destination.slack_channel_id."):
    ids = ids or {
        "input": "ChatInput-Ref01", "mcp": "MCPTools-Ref02",
        "agent": "Agent-Ref03", "output": "ChatOutput-Ref04",
    }
    nodes = [
        node(ids["input"], "ChatInput", "Chat Input", {
            "code": code_field(IO_CODE.format(name="ChatInput", display="Chat Input")),
            "input_value": field("MultilineInput"),
            "session_id": field("MessageTextInput"),
        }, [("message", ["Message"])]),
        node(ids["mcp"], "MCPTools", "MCP Tools", {
            "code": code_field(MCP_CODE),
            "mcp_server": field("McpInput", "mcp", {}),
            "tool": field("DropdownInput", value="SLACK_SEND_MESSAGE"),
        }, [("component_as_tool", ["Tool"])]),
        node(ids["agent"], "Agent", "Runner", {
            "code": code_field(AGENT_CODE),
            "input_value": field("MessageInput"),
            "system_prompt": field("MultilineInput", value=prompt),
            "tools": field("HandleInput", "other"),
            "api_key": field("SecretStrInput", value="OPENAI_API_KEY", load_from_db=True, password=True),
        }, [("response", ["Message"])]),
        node(ids["output"], "ChatOutput", "Chat Output", {
            "code": code_field(IO_CODE.format(name="ChatOutput", display="Chat Output")),
            "input_value": field("MessageInput"),
        }, [("message", ["Message"])]),
    ]
    edges = [
        edge(ids["input"], "ChatInput", "message", ["Message"], ids["agent"], "input_value", ["Message"]),
        edge(ids["mcp"], "MCPTools", "component_as_tool", ["Tool"], ids["agent"], "tools", ["Tool"], "other"),
        edge(ids["agent"], "Agent", "response", ["Message"], ids["output"], "input_value", ["Message"]),
    ]
    return {
        "id": "00000000-0000-4000-8000-000000000000",
        "name": "Flow 3 reference (sanitized)",
        "description": "",
        "data": {"nodes": nodes, "edges": edges, "viewport": {"x": 0, "y": 0, "zoom": 1}},
    }


def bob_flow():
    """A correct generated variant: new IDs, rewritten prompt, docs link,
    placeholders, CRLF line endings and trailing spaces in the code."""
    flow = reference_flow(
        ids={"input": "ChatInput-Bob9a", "mcp": "MCPTools-Bob9b", "agent": "Agent-Bob9c", "output": "ChatOutput-Bob9d"},
        prompt=(
            "Read approved_destination.slack_channel_id from the input and post there only.\n"
            "See https://docs.langflow.org/mcp-client and https://docs.composio.dev/tools/slack.\n"
            "Repository: <owner/repo>. Channel: {{approved_destination.slack_channel_id}}."
        ),
    )
    flow["name"] = "Flow 3 (generated)"
    mcp = find(flow, "MCPTools")
    code = mcp["data"]["node"]["template"]["code"]["value"]
    mcp["data"]["node"]["template"]["code"]["value"] = "\r\n".join(line + "  " for line in code.split("\n"))
    for i, n in enumerate(flow["data"]["nodes"]):
        n["position"] = {"x": 300 * i, "y": 40}
    return flow


def find(flow, ctype):
    return next(n for n in flow["data"]["nodes"] if n["data"]["type"] == ctype)


def template(flow, ctype):
    return find(flow, ctype)["data"]["node"]["template"]


def clone(flow):
    return copy.deepcopy(flow)


FAKE = {
    "openai": "sk-" + "proj-" + "A1b2C3d4" * 4,
    "slack": "xox" + "b-" + "1234567890-" + "abcdefghij" * 2,
    "bearer": "Bearer " + "q7W9e2R4t6Y8u0I1" * 2,
    "mcp_url": "https://" + "backend.composio.dev/v3/mcp/" + "9f8e7d6c-1a2b-4c3d-8e9f-0a1b2c3d4e5f" + "/mcp?user_id=" + "user42",
    "channel": "C" + "0A1B2C3D4E",
    "webhook": "https://hooks.slack.com/" + "services/T000/B000/" + "XXXXXXXXXXXX",
}
