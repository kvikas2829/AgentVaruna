"""Back ends for the demo agents. Every tool is served by one of Meridian Freight's stubbed MCP servers
(see meridian/mcp_servers.py); the agents call the same functions in-process. Nothing here touches a real system."""


def _all() -> dict:
    from .meridian.mcp_servers import FUNCTIONS
    return dict(FUNCTIONS)


IMPLS = {"travel": _all, "legal": _all, "soc": _all, "itadmin": _all}
