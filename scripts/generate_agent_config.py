"""Generate a separate review candidate through an explicitly selected LLM adapter."""

import argparse
from pathlib import Path

from agent_adapter_service.agent.config.generator import CommandLlmProvider, generate_candidate

try:
    from ._common import catalog_registry, run, tool_metadata
except ImportError:
    from _common import catalog_registry, run, tool_metadata


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--business-description", required=True, type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--approved-dir", type=Path, default=Path("configs/agents/customer_service")
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument(
        "--llm-command",
        required=True,
        nargs=argparse.REMAINDER,
        help="Executable and arguments; must be the last option (JSON stdin/stdout)",
    )
    args = parser.parse_args()
    config = await generate_candidate(
        CommandLlmProvider(args.llm_command, timeout=args.timeout),
        business_description=args.business_description.read_text(encoding="utf-8"),
        model=args.model,
        tools=tool_metadata(catalog_registry()),
        approved_directory=args.approved_dir,
        output_directory=args.output_dir,
    )
    return {
        "status": "candidate_created",
        "agent_id": config.agent.id,
        "directory": str(args.output_dir),
        "review_required": True,
    }


if __name__ == "__main__":
    run(main)
