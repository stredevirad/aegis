"""CLI Entrypoint for running the Aegis MCP Server over standard I/O (stdio).

Usage:
    python -m src.adapters.mcp [--live-telemetry] [--db <path>]
"""
import argparse
import asyncio
import sys
from pathlib import Path

from src.adapters.mcp import create_mcp_server
from src.persistence.repository import SQLiteRepository


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aegis Model Context Protocol (MCP) Server")
    parser.add_argument(
        "--live-telemetry",
        action="store_true",
        help="Connect to live Prometheus, OpenSearch, and Jaeger endpoints",
    )
    parser.add_argument(
        "--db",
        type=str,
        default="aegis.db",
        help="Path to SQLite persistence database (default: aegis.db)",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    
    repository = SQLiteRepository(db_path=args.db)

    if args.live_telemetry:
        import yaml

        from src.adapters.jaeger import JaegerProvider
        from src.adapters.mock_providers import MockTopologyProvider
        from src.adapters.opensearch import OpenSearchProvider
        from src.adapters.prometheus import PrometheusProvider

        config_path = Path(__file__).parent.parent.parent.parent / "config" / "opentelemetry-demo.yaml"
        metrics_endpoint = "http://localhost:9090"
        logs_endpoint = "http://localhost:9200"
        traces_endpoint = "http://localhost:16686"

        if config_path.exists():
            with open(config_path, "r") as f:
                config = yaml.safe_load(f)
                metrics_endpoint = config.get("telemetry", {}).get("metrics", {}).get("endpoint", metrics_endpoint)
                logs_endpoint = config.get("telemetry", {}).get("logs", {}).get("endpoint", logs_endpoint)
                traces_endpoint = config.get("telemetry", {}).get("traces", {}).get("endpoint", traces_endpoint)

        metrics_provider = PrometheusProvider(endpoint=metrics_endpoint)
        logs_provider = OpenSearchProvider(endpoint=logs_endpoint)
        traces_provider = JaegerProvider(endpoint=traces_endpoint)
        topology_provider = MockTopologyProvider()

        server = create_mcp_server(
            metrics_provider=metrics_provider,
            logs_provider=logs_provider,
            topology_provider=topology_provider,
            traces_provider=traces_provider,
            repository=repository,
        )
    else:
        server = create_mcp_server(repository=repository)

    # Run MCP server over stdio transport
    await server.mcp.run_stdio_async()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
