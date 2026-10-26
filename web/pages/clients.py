# SPDX-License-Identifier: Apache-2.0
"""How each agent or MCP client connects to G6's MCP server (``g6-mcp``, over stdio), for the Get started page.

Every command and snippet is the shape the client's own documentation gives (``docs``), checked 2026-10-19. A client
that changes its format changes here, and tests/site/test_get_started.py parses each snippet.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Client:
    key: str
    name: str
    cli: str            # a one-line command that registers G6, or "" when the client has none
    file: str           # where the configuration lives
    lang: str           # json | toml | yaml
    snippet: str
    docs: str
    note: str = ""


_STDIO = '{\n  "mcpServers": {\n    "g6": { "type": "stdio", "command": "g6-mcp", "args": [] }\n  }\n}'
_PLAIN = '{\n  "mcpServers": {\n    "g6": { "command": "g6-mcp", "args": [] }\n  }\n}'

CLIENTS: tuple[Client, ...] = (
    Client("claude_code", "Claude Code", "claude mcp add --transport stdio --scope user g6 -- g6-mcp",
           ".mcp.json in a project (or let the command write ~/.claude.json)", "json", _STDIO,
           "https://code.claude.com/docs/en/mcp"),
    Client("codex", "Codex", "codex mcp add g6 -- g6-mcp", "~/.codex/config.toml", "toml",
           '[mcp_servers.g6]\ncommand = "g6-mcp"', "https://developers.openai.com/codex/mcp"),
    Client("opencode", "OpenCode", "", "opencode.json in a project, or ~/.config/opencode/opencode.json", "json",
           '{\n  "$schema": "https://opencode.ai/config.json",\n  "mcp": {\n'
           '    "g6": { "type": "local", "command": ["g6-mcp"], "enabled": true }\n  }\n}',
           "https://opencode.ai/docs/mcp-servers/", "OpenCode's own `opencode mcp add` asks its questions one by one."),
    Client("hermes", "Hermes", "hermes mcp add g6 --command g6-mcp", "~/.hermes/config.yaml", "yaml",
           "mcp_servers:\n  g6:\n    command: g6-mcp\n    args: []\n    enabled: true",
           "https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp",
           "Reload with /reload-mcp, or restart Hermes."),
    Client("muse", "Muse Code", "", "~/.config/muse/settings.json", "json",
           '{\n  "schema_version": 1,\n  "mcp_servers": {\n'
           '    "g6": { "transport": "stdio", "command": "g6-mcp", "args": [] }\n  }\n}',
           "https://dev.meta.ai/docs/muse-code/extending", "Keep \"schema_version\": 1 in the file."),
    Client("cursor", "Cursor", "", "~/.cursor/mcp.json (or .cursor/mcp.json in a project)", "json", _STDIO,
           "https://cursor.com/docs/context/mcp"),
    Client("vscode", "VS Code", 'code --add-mcp "{\\"name\\":\\"g6\\",\\"command\\":\\"g6-mcp\\"}"',
           ".vscode/mcp.json in a project", "json",
           '{\n  "servers": {\n    "g6": { "type": "stdio", "command": "g6-mcp", "args": [] }\n  }\n}',
           "https://code.visualstudio.com/docs/copilot/customization/mcp-servers", "For Copilot's agent mode."),
    Client("gemini", "Gemini CLI", "gemini mcp add -s user g6 g6-mcp", "~/.gemini/settings.json", "json", _PLAIN,
           "https://geminicli.com/docs/tools/mcp-server/"),
    Client("windsurf", "Windsurf", "", "mcp_config.json: open it from Cascade's menu, “Open MCP config file”",
           "json", _PLAIN, "https://docs.devin.ai/desktop/cascade/mcp", "Windsurf is now called Devin Desktop."),
    Client("zed", "Zed", "", "Zed's settings.json (Settings, then AI, then MCP Servers)", "json",
           '{\n  "context_servers": {\n    "g6": { "command": "g6-mcp", "args": [], "env": {} }\n  }\n}',
           "https://zed.dev/docs/ai/mcp"),
    Client("claude_desktop", "Claude Desktop", "",
           "%APPDATA%\\Claude\\claude_desktop_config.json (Windows) or "
           "~/Library/Application Support/Claude/claude_desktop_config.json (macOS)", "json", _PLAIN,
           "https://modelcontextprotocol.io/docs/develop/connect-local-servers", "Quit it fully and open it again."),
)


@dataclass(frozen=True)
class Model:
    """The lines, for the person's key file, that point G6's own model calls at one model;
    tests/site/test_get_started.py writes each one there and builds it through ``g6.core.llm.provider_from_env``."""
    key: str
    name: str
    snippet: str        # one NAME=value per line
    docs: str = ""
    note: str = ""


MODELS: tuple[Model, ...] = (
    Model("ollama", "Ollama (free, local)", "G6_LLM_PROVIDER=ollama\nG6_LLM_MODEL=gpt-oss:20b",
          "https://ollama.com/library/gpt-oss", "Pull it first: ollama pull gpt-oss:20b."),
    Model("jev", "Jev (OpenRouter)",
          "G6_LLM_PROVIDER=openrouter\nG6_LLM_MODEL=typesafe/jev-router\nOPENROUTER_API_KEY=your-openrouter-key",
          "https://openrouter.ai/typesafe/jev-router",
          "Jev Router picks a model and reasoning effort for each request, so its price follows the model it picks. "
          "Tuning compares runs, so a fixed model gives steadier numbers there; any OpenRouter model id works the "
          "same way."),
    Model("anthropic", "Anthropic", "G6_LLM_PROVIDER=anthropic\nG6_LLM_MODEL=claude-opus-5-5\n"
          "ANTHROPIC_API_KEY=your-anthropic-key", note='Install the extra first: pip install ".[anthropic]".'),
    Model("openai", "OpenAI", "G6_LLM_PROVIDER=openai\nG6_LLM_MODEL=your-model\nOPENAI_API_KEY=your-openai-key"),
)
