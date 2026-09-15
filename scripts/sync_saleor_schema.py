"""Fetch GraphQL introspection and atomically write validated SDL for code generation."""

import argparse
import os
import tempfile
from pathlib import Path

import httpx
from graphql import build_client_schema, get_introspection_query, print_schema

try:
    from ._common import run
except ImportError:
    from _common import run


async def sync_schema(endpoint: str, token: str | None, output: Path, *, client=None, timeout=30):
    url = httpx.URL(endpoint)
    if (
        url.scheme not in {"http", "https"}
        or not url.host
        or url.userinfo
        or url.query
        or url.fragment
    ):
        raise ValueError("Expected HTTP(S) endpoint without embedded credentials")
    if timeout <= 0:
        raise ValueError("Timeout must be positive")

    async def fetch(http):
        response = await http.post(
            endpoint,
            json={"query": get_introspection_query(descriptions=True)},
            headers={"Authorization": f"Bearer {token}"} if token else {},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors") or not payload.get("data"):
            raise ValueError("Introspection failed")
        return print_schema(build_client_schema(payload["data"])) + "\n"

    if client is None:
        async with httpx.AsyncClient(follow_redirects=False) as http:
            sdl = await fetch(http)
    else:
        sdl = await fetch(client)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output.parent, suffix=".graphql", delete=False
        ) as file:
            temporary = Path(file.name)
            file.write(sdl)
        temporary.replace(output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=os.environ.get("SALEOR_API_URL"))
    parser.add_argument("--token", help="Bearer token; prefer --token-env to avoid shell history")
    parser.add_argument("--token-env", default="SALEOR_SERVICE_TOKEN")
    parser.add_argument("--output", type=Path, default=Path(".cache/saleor/schema.graphql"))
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    if not args.endpoint:
        parser.error("--endpoint or SALEOR_API_URL is required")
    await sync_schema(
        args.endpoint,
        args.token or os.environ.get(args.token_env),
        args.output,
        timeout=args.timeout,
    )
    return {"status": "synchronized", "output": str(args.output)}


if __name__ == "__main__":
    run(main)
