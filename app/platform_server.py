"""Local-only administration and WSGI entry point for the C3 platform."""
from __future__ import annotations

import argparse
import getpass
import json
from pathlib import Path
from wsgiref.simple_server import make_server

from .product_platform.bootstrap import create_runtime


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="IELTS Writing AI Coach C3 platform")
    parser.add_argument("--data-root", type=Path, required=True)
    subcommands = parser.add_subparsers(dest="command", required=True)
    initialise = subcommands.add_parser("init-tenant")
    initialise.add_argument("--name", required=True)
    initialise.add_argument("--email", required=True)
    serve = subcommands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "localhost", "::1"))
    serve.add_argument("--port", type=int, default=8765)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    runtime = create_runtime(args.data_root)
    try:
        if args.command == "init-tenant":
            password = getpass.getpass("Admin password (12+ characters): ")
            confirmation = getpass.getpass("Confirm password: ")
            if password != confirmation:
                raise SystemExit("Passwords do not match.")
            principal = runtime.identity.create_tenant_admin(args.name, args.email, password)
            print(json.dumps({
                "tenantId": principal.tenant_id,
                "adminUserId": principal.user_id,
                "role": principal.role.value,
            }, sort_keys=True))
            return 0
        if args.command == "serve":
            if not (1 <= args.port <= 65535):
                raise SystemExit("Port is out of range.")
            with make_server(args.host, args.port, runtime.wsgi) as server:
                print(json.dumps({
                    "apiVersion": "v1",
                    "host": args.host,
                    "port": args.port,
                    "privateRagLoaded": False,
                }, sort_keys=True))
                server.serve_forever()
            return 0
        return 2
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
