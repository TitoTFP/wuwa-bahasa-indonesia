from __future__ import annotations

import argparse
from pathlib import Path

from .core import (
    DEFAULT_BASE_URL,
    DEFAULT_DRAFT_DIR,
    DEFAULT_EN_CSV,
    DEFAULT_GLOSSARY,
    DEFAULT_ID_CSV,
    DEFAULT_MODEL,
    DEFAULT_PROMPT,
    apply_draft,
    qa_draft,
    translate_to_draft,
)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m tools.llm_translate")
    subparsers = parser.add_subparsers(dest="command", required=True)

    translate = subparsers.add_parser("translate", help="Create an LLM draft CSV")
    translate.add_argument("--en-csv", type=Path, default=DEFAULT_EN_CSV)
    translate.add_argument("--id-csv", type=Path, default=DEFAULT_ID_CSV)
    translate.add_argument("--output", type=Path)
    translate.add_argument("--draft-dir", type=Path, default=DEFAULT_DRAFT_DIR)
    translate.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT)
    translate.add_argument("--glossary", type=Path, default=DEFAULT_GLOSSARY)
    translate.add_argument("--base-url", default=None, help=f"default: env LMSTUDIO_BASE_URL or {DEFAULT_BASE_URL}")
    translate.add_argument("--model", default=None, help=f"default: env LMSTUDIO_MODEL or {DEFAULT_MODEL}")
    translate.add_argument("--limit", type=int)
    translate.add_argument("--batch-size", type=int, default=4)
    translate.add_argument("--retries", type=int, default=2)

    apply = subparsers.add_parser("apply", help="Apply approved draft rows")
    apply.add_argument("draft_csv", type=Path)
    apply.add_argument("--id-csv", type=Path, default=DEFAULT_ID_CSV)
    apply.add_argument("--output", type=Path)
    apply.add_argument("--write", action="store_true", help="Write changes; default is dry-run")

    qa = subparsers.add_parser("qa", help="Validate a draft CSV")
    qa.add_argument("draft_csv", type=Path)

    args = parser.parse_args()
    if args.command == "translate":
        result = translate_to_draft(
            en_csv=args.en_csv,
            id_csv=args.id_csv,
            output=args.output,
            draft_dir=args.draft_dir,
            prompt_path=args.prompt,
            glossary_path=args.glossary,
            base_url=args.base_url,
            model=args.model,
            limit=args.limit,
            batch_size=args.batch_size,
            retries=args.retries,
        )
    elif args.command == "apply":
        result = apply_draft(
            args.draft_csv,
            id_csv=args.id_csv,
            output_csv=args.output,
            dry_run=not args.write,
        )
    else:
        result = qa_draft(args.draft_csv)

    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
