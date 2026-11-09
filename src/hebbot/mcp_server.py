"""Hebbot MCP server — the one agent-facing API of the substrate.

Per foundations.md commitments #5 + #10:
  - One MCP server, not many.
  - Tool list is generated dynamically from the skill store. Adding a skill
    means inserting a row; the orchestrator sees a new tool on its next
    list_tools call.
  - Skill body-type changes (script → trajectory → learned_policy) are
    transparent — the MCP tool stays the same, only the body the substrate
    runs underneath changes.

Run via stdio (for Claude Code / other MCP clients):
    hebbot-mcp --data-dir ~/hebbot-data --franka 192.168.0.253

To skip hardware (mock mode for development / CI):
    hebbot-mcp --data-dir /tmp/hebbot-dev --no-robot
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Any, Optional

from .substrate import Substrate, SkillNotFoundError, UnsupportedBodyTypeError


logger = logging.getLogger("hebbot.mcp")


# ---------------------------------------------------------------------------
# Hardware bootstrap
# ---------------------------------------------------------------------------

def make_hardware_context(
    franka_host: Optional[str],
    no_robot: bool,
    no_camera: bool = False,
    camera_settle: float = 5.0,
) -> dict:
    """Build the context dict that gets passed to skills.

    Populates "panda"/"gripper" (arm) and "cameras" (perception) independently;
    each is best-effort. Skills check for the key they need and fall back to
    mock mode if it's absent, so a missing robot doesn't disable cameras and
    vice versa.
    """
    ctx: dict[str, Any] = {}

    if no_robot or not franka_host:
        logger.info("Robot disabled (--no-robot or no host); arm skills run in mock mode.")
    else:
        try:
            import panda_py
            ctx["panda"] = panda_py.Panda(franka_host)
            logger.info(f"Connected to Franka at {franka_host}")
            try:
                ctx["gripper"] = panda_py.libfranka.Gripper(franka_host)
                logger.info("Gripper connected.")
            except Exception as e:
                logger.warning(f"Failed to connect to Gripper: {e}; arm-only mode.")
        except ImportError:
            logger.warning("panda-python not installed; arm skills run in mock mode.")
        except Exception as e:
            logger.warning(f"Failed to connect to Franka at {franka_host}: {e}; arm in mock mode.")

    if no_camera:
        logger.info("Cameras disabled (--no-camera); read_camera runs in mock mode.")
    else:
        try:
            from .perception.realsense import open_realsense_cameras
            cameras = open_realsense_cameras(settle=camera_settle)
            if cameras:
                ctx["cameras"] = cameras
                logger.info(f"Cameras connected: {sorted(cameras)}")
            else:
                logger.info("No RealSense cameras found; read_camera runs in mock mode.")
        except ImportError:
            logger.warning("pyrealsense2 not installed; read_camera runs in mock mode.")
        except Exception as e:
            logger.warning(f"Camera bootstrap failed: {e}; read_camera runs in mock mode.")

    return ctx


# ---------------------------------------------------------------------------
# MCP server (stdio transport)
# ---------------------------------------------------------------------------

async def serve_stdio(substrate: Substrate, hardware_ctx: dict) -> None:
    """Run an MCP server on stdio, exposing the substrate's skill store."""
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp.types import TextContent, Tool
    except ImportError as e:
        print(
            "The 'mcp' Python package is required. Install with:\n"
            "    uv pip install mcp\n"
            f"(original error: {e})",
            file=sys.stderr,
        )
        sys.exit(2)

    server = Server("hebbot")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        skills = substrate.list_skills()
        tools: list[Tool] = []
        for sk in skills:
            schema = sk.get("params_schema") or {
                "type": "object", "properties": {}, "additionalProperties": False,
            }
            description = f"Hebbot skill: {sk['name']} (body={sk['body_type']}, v{sk['version']})"
            tools.append(Tool(
                name=sk["name"],
                description=description,
                inputSchema=schema,
            ))
        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: dict | None) -> list[TextContent]:
        arguments = arguments or {}
        logger.info(f"call_tool: {name} args={arguments}")
        try:
            outcome = substrate.call(name, params=arguments, context=hardware_ctx)
            return [TextContent(type="text", text=json.dumps(outcome, default=str, indent=2))]
        except SkillNotFoundError as e:
            return [TextContent(type="text",
                                text=json.dumps({"success": False, "error": f"Skill not found: {e}"}))]
        except UnsupportedBodyTypeError as e:
            return [TextContent(type="text",
                                text=json.dumps({"success": False, "error": f"Body type not supported: {e}"}))]
        except Exception as e:
            logger.exception(f"call_tool {name} failed")
            return [TextContent(type="text",
                                text=json.dumps({"success": False, "error": f"{type(e).__name__}: {e}"}))]

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Hebbot MCP server")
    parser.add_argument("--data-dir", default="~/hebbot-data",
                        help="Hebbot data directory (default: ~/hebbot-data)")
    parser.add_argument("--franka", default="192.168.0.253",
                        help="Franka FCI hostname/IP (default: 192.168.0.253)")
    parser.add_argument("--no-robot", action="store_true",
                        help="Skip the arm connection; arm skills run in mock mode.")
    parser.add_argument("--no-camera", action="store_true",
                        help="Skip camera connections; read_camera runs in mock mode.")
    parser.add_argument("--camera-settle", type=float, default=5.0,
                        help="Seconds to wait after hardware_reset before starting "
                             "camera pipelines (finding #101; default 5.0).")
    parser.add_argument("--register-primitives", action="store_true",
                        help="Register the built-in primitives into the skill store before serving.")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    substrate = Substrate(args.data_dir)

    if args.register_primitives:
        from .skills.primitives import register_all as register_primitives
        from .skills.composed import register_all as register_composed
        from .skills.selfcal import register_all as register_selfcal
        register_primitives(substrate)
        register_composed(substrate)
        register_selfcal(substrate)
        from .skills.touch_stack import register_all as register_touch_stack
        register_touch_stack(substrate)
        logger.info(f"Registered primitives. Skill count: {len(substrate.list_skills())}")

    hardware_ctx = make_hardware_context(
        args.franka, args.no_robot,
        no_camera=args.no_camera, camera_settle=args.camera_settle,
    )

    try:
        asyncio.run(serve_stdio(substrate, hardware_ctx))
    except KeyboardInterrupt:
        logger.info("Shutdown.")
    finally:
        substrate.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
