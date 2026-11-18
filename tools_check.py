"""Print the curated tool list, or call one tool directly, without starting a server.

    python tools_check.py
    python tools_check.py --call assets_host-list --args '{"limit": 1}'
"""

import argparse
import asyncio
import json

from jumpserver_mcp_server.config import settings
from jumpserver_mcp_server.server import create_app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--call", help="tool name to invoke")
    parser.add_argument("--args", default="{}", help="JSON arguments for the tool")
    options = parser.parse_args()

    app = create_app()
    mcp = app.state.mcp
    if not options.call:
        by_group: dict[str, list[str]] = {}
        for tool in mcp.tools:
            op = mcp.operation_map.get(tool.name, {})
            method = str(op.get("method", "get")).upper()
            path = str(op.get("path", ""))
            by_group.setdefault("工具", []).append(f"{method:6} {tool.name:48} {path}")
        for line in sorted(by_group["工具"]):
            print(line)
        print(f"\n共 {len(mcp.tools)} 个 MCP 工具")
        return

    result = asyncio.run(mcp.executor.execute(options.call, json.loads(options.args)))
    print(result[0].text)


if __name__ == "__main__":
    main()
