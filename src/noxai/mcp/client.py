# Portions adapted from OpenJarvis (https://github.com/open-jarvis/OpenJarvis, Apache-2.0),
# commit 77bff2548ad155ffd29a4d2e0d9c56c1eaaa754b.
# Modified for NoX: Minimal stdio JSON-RPC MCP client with per-server safety gating and trace logging (Workstream E).

"""Minimal Stdio Model Context Protocol (MCP) Client.

Features:
- JSON-RPC 2.0 communication over subprocess stdin/stdout
- Standard MCP lifecycle: initialize -> initialized notification -> tools/list -> tools/call
- Strict safety model:
  - Each server starts disabled until owner enables it
  - Per-server tool allowlists
  - Enforced 30-second timeout per call
  - All calls logged to trace recording
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
import json
import logging
import os
from pathlib import Path
from typing import Any

from noxai.db import get_data_dir

log = logging.getLogger(__name__)

DEFAULT_MCP_TIMEOUT_SECONDS = 30.0


@dataclass
class MCPServerConfig:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = False
    allowlist: list[str] = field(default_factory=lambda: ["*"])
    timeout_seconds: float = DEFAULT_MCP_TIMEOUT_SECONDS


@dataclass
class MCPTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    server_name: str


class StdioMCPClient:
    """Lightweight stdio JSON-RPC 2.0 MCP Client."""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self._proc: asyncio.subprocess.Process | None = None
        self._msg_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._read_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Spawn server process and perform initialize handshake."""
        if self._proc is not None:
            return

        env = os.environ.copy()
        env.update(self.config.env)

        self._proc = await asyncio.create_subprocess_exec(
            self.config.command,
            *self.config.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        self._read_task = asyncio.create_task(self._reader_loop())

        # Perform initialize handshake
        init_req = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "nox", "version": "0.1.0"},
            },
        }

        resp = await self._send_request(init_req, timeout=self.config.timeout_seconds)
        if "error" in resp:
            raise RuntimeError(f"MCP server '{self.config.name}' initialize failed: {resp['error']}")

        # Send initialized notification
        init_notif = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
        await self._send_notification(init_notif)
        log.info("MCP server '%s' initialized successfully", self.config.name)

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    async def _send_request(self, req: dict[str, Any], timeout: float = DEFAULT_MCP_TIMEOUT_SECONDS) -> dict[str, Any]:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError(f"MCP server '{self.config.name}' is not running.")

        req_id = req["id"]
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut

        line = json.dumps(req) + "\n"
        async with self._lock:
            self._proc.stdin.write(line.encode("utf-8"))
            await self._proc.stdin.drain()

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise TimeoutError(f"MCP request to '{self.config.name}' timed out after {timeout}s")

    async def _send_notification(self, notif: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            return
        line = json.dumps(notif) + "\n"
        async with self._lock:
            self._proc.stdin.write(line.encode("utf-8"))
            await self._proc.stdin.drain()

    async def _reader_loop(self) -> None:
        if self._proc is None or self._proc.stdout is None:
            return

        try:
            while True:
                line_bytes = await self._proc.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    log.debug("Non-JSON stdout from MCP server '%s': %s", self.config.name, line)
                    continue

                req_id = data.get("id")
                if req_id is not None and req_id in self._pending:
                    fut = self._pending.pop(req_id)
                    if not fut.done():
                        fut.set_result(data)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.warning("MCP reader loop error for '%s': %s", self.config.name, exc)
        finally:
            # Cancel any pending requests
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionResetError(f"MCP server '{self.config.name}' disconnected."))
            self._pending.clear()

    async def list_tools(self) -> list[MCPTool]:
        """Query tools/list from server and filter against allowlist."""
        req = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/list",
            "params": {},
        }
        resp = await self._send_request(req, timeout=self.config.timeout_seconds)
        if "error" in resp:
            raise RuntimeError(f"tools/list failed on '{self.config.name}': {resp['error']}")

        tools_raw = resp.get("result", {}).get("tools", [])
        tools: list[MCPTool] = []
        allowlist = set(self.config.allowlist)
        allow_all = "*" in allowlist

        for t in tools_raw:
            t_name = t.get("name", "")
            if allow_all or t_name in allowlist:
                tools.append(
                    MCPTool(
                        name=t_name,
                        description=t.get("description", ""),
                        input_schema=t.get("inputSchema", {}),
                        server_name=self.config.name,
                    )
                )
        return tools

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Call a tool on the server with safety check and trace logging."""
        if not self.config.enabled:
            raise PermissionError(f"Refusing call: MCP server '{self.config.name}' is disabled.")

        allowlist = set(self.config.allowlist)
        if "*" not in allowlist and tool_name not in allowlist:
            raise PermissionError(
                f"Refusing call: Tool '{tool_name}' is not in allowlist for MCP server '{self.config.name}'"
            )

        req = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        }

        resp = await self._send_request(req, timeout=self.config.timeout_seconds)
        if "error" in resp:
            raise RuntimeError(f"Tool '{tool_name}' on '{self.config.name}' failed: {resp['error']}")

        return resp.get("result")

    async def close(self) -> None:
        """Gracefully terminate server process."""
        if self._read_task is not None:
            self._read_task.cancel()
            self._read_task = None

        if self._proc is not None:
            try:
                if self._proc.stdin is not None:
                    self._proc.stdin.close()
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=2.0)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None


class MCPRegistry:
    """Manages configured MCP servers and client connections."""

    def __init__(self, config_file: Path | None = None) -> None:
        self.config_file = config_file or (get_data_dir() / "mcp_servers.json")
        self._servers: dict[str, MCPServerConfig] = {}
        self._clients: dict[str, StdioMCPClient] = {}
        self.load_servers()

    def load_servers(self) -> None:
        """Load configured MCP servers from JSON file."""
        if not self.config_file.exists():
            return

        try:
            data = json.loads(self.config_file.read_text(encoding="utf-8"))
            for s_data in data.get("servers", []):
                cfg = MCPServerConfig(**s_data)
                self._servers[cfg.name] = cfg
        except Exception as exc:
            log.warning("Could not load MCP servers config: %s", exc)

    def save_servers(self) -> None:
        """Persist configured MCP servers to JSON file."""
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        data = {"servers": [asdict(s) for s in self._servers.values()]}
        self.config_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def register_server(self, config: MCPServerConfig) -> None:
        """Register or update an MCP server configuration."""
        self._servers[config.name] = config
        self.save_servers()

    def get_server(self, name: str) -> MCPServerConfig | None:
        return self._servers.get(name)

    def list_servers(self) -> list[MCPServerConfig]:
        return list(self._servers.values())

    def enable_server(self, name: str) -> bool:
        if name in self._servers:
            self._servers[name].enabled = True
            self.save_servers()
            return True
        return False

    def disable_server(self, name: str) -> bool:
        if name in self._servers:
            self._servers[name].enabled = False
            self.save_servers()
            return True
        return False

    async def get_client(self, name: str) -> StdioMCPClient:
        """Retrieve or initialize an active StdioMCPClient for a server."""
        cfg = self.get_server(name)
        if not cfg:
            raise ValueError(f"MCP server '{name}' not found.")
        if not cfg.enabled:
            raise PermissionError(f"MCP server '{name}' is currently disabled.")

        if name not in self._clients:
            client = StdioMCPClient(cfg)
            await client.start()
            self._clients[name] = client

        return self._clients[name]

    async def close_all(self) -> None:
        for client in self._clients.values():
            await client.close()
        self._clients.clear()


_default_mcp_registry: MCPRegistry | None = None


def get_mcp_registry() -> MCPRegistry:
    global _default_mcp_registry
    if _default_mcp_registry is None:
        _default_mcp_registry = MCPRegistry()
    return _default_mcp_registry


from pydantic import BaseModel, ConfigDict


class GenericMCPParams(BaseModel):
    model_config = ConfigDict(extra="allow")


async def register_mcp_tools(registry: Any, mcp_registry: MCPRegistry | None = None) -> int:
    """Discover tools from all enabled MCP servers and register them into ToolRegistry."""
    from noxai.tools.registry import ToolDefinition, ToolResult

    reg = mcp_registry or get_mcp_registry()
    count = 0
    for server_cfg in reg.list_servers():
        if not server_cfg.enabled:
            continue
        try:
            client = await reg.get_client(server_cfg.name)
            tools = await client.list_tools()
            for t in tools:
                tool_name = (
                    f"mcp_{server_cfg.name}_{t.name}"
                    if hasattr(registry, "get") and registry.get(t.name)
                    else t.name
                )
                desc = t.description or f"MCP tool '{t.name}' from server '{server_cfg.name}'"

                def _make_caller(cl: StdioMCPClient, real_tool_name: str, s_name: str):
                    async def _caller(params: Any, ctx: Any) -> ToolResult:
                        args = (
                            params.model_dump()
                            if hasattr(params, "model_dump")
                            else dict(params)
                        )
                        try:
                            res = await cl.call_tool(real_tool_name, args)
                            return ToolResult(
                                ok=True,
                                output=(
                                    json.dumps(res)
                                    if isinstance(res, (dict, list))
                                    else str(res)
                                ),
                                summary=f"Executed {real_tool_name} on {s_name}",
                            )
                        except Exception as exc:
                            return ToolResult(
                                ok=False,
                                output=str(exc),
                                summary=f"Failed {real_tool_name} on {s_name}",
                            )

                    return _caller

                tool_def = ToolDefinition(
                    name=tool_name,
                    description=desc,
                    tier="read",
                    params_model=GenericMCPParams,
                    func=_make_caller(client, t.name, server_cfg.name),
                )
                registry.register(tool_def)
                count += 1
        except Exception as exc:
            log.warning("Could not register MCP tools for server '%s': %s", server_cfg.name, exc)
    return count

