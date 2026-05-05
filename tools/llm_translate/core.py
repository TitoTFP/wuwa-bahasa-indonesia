from __future__ import annotations

import csv
import json
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

DEFAULT_BASE_URL = "http://127.0.0.1:1234/v1"
DEFAULT_MODEL = "google/gemma-4-e4b"
DEFAULT_EN_CSV = Path("TextGame/en/MultiText_EN.csv")
DEFAULT_ID_CSV = Path("TextGame/id/MultiText_ID.csv")
DEFAULT_DRAFT_DIR = Path("llm_drafts")
PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_PROMPT = PACKAGE_DIR / "prompt.md"
DEFAULT_GLOSSARY = PACKAGE_DIR / "glossary.yaml"
DRAFT_COLUMNS = ["Id", "source", "current_id", "suggestion", "status", "issues"]

TAG_RE = re.compile(r"<[^>]+>")
BRACE_RE = re.compile(r"\{[^}]+\}")
PERCENT_RE = re.compile(r"%(?:\d+\$)?[sdif]")


@dataclass(frozen=True)
class TranslationJob:
    row_id: str
    source: str
    current_id: str
    memory_suggestion: str = ""


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def validate_translation(source: str, suggestion: str) -> list[str]:
    issues: list[str] = []
    if not source:
        if suggestion:
            issues.append("empty source should stay empty")
        return issues
    if not suggestion:
        issues.append("missing suggestion")

    for token in _unique(TAG_RE.findall(source)):
        if token not in suggestion:
            issues.append(f"missing tag: {token}")
    for token in _unique(BRACE_RE.findall(source)):
        if token not in suggestion:
            issues.append(f"missing placeholder: {token}")
    for token in _unique(PERCENT_RE.findall(source)):
        if token not in suggestion:
            issues.append(f"missing placeholder: {token}")
    if _newline_count(source) != _newline_count(suggestion):
        issues.append("newline count mismatch")
    return issues


def build_translation_jobs(
    en_rows: list[dict[str, str]],
    id_rows: list[dict[str, str]],
    *,
    missing_only: bool = True,
    limit: int | None = None,
) -> list[TranslationJob]:
    id_by_key = {row["Id"]: row for row in id_rows}
    memory = _build_translation_memory(en_rows, id_rows)
    jobs: list[TranslationJob] = []

    for row in en_rows:
        row_id = row["Id"]
        source = row.get("Content", "")
        current = id_by_key.get(row_id, {}).get("Content", "")
        if not source:
            continue
        if missing_only and current and current != source:
            continue
        jobs.append(
            TranslationJob(
                row_id=row_id,
                source=source,
                current_id=current,
                memory_suggestion=memory.get(source, ""),
            )
        )
        if limit is not None and len(jobs) >= limit:
            break
    return jobs


def extract_translations(payload: dict[str, Any]) -> dict[str, str]:
    content = payload["choices"][0]["message"]["content"]
    parsed = _loads_json_object(content)
    translations = parsed.get("translations", [])
    return {str(item["id"]): str(item["translation"]) for item in translations}


def translate_to_draft(
    *,
    en_csv: Path = DEFAULT_EN_CSV,
    id_csv: Path = DEFAULT_ID_CSV,
    output: Path | None = None,
    draft_dir: Path = DEFAULT_DRAFT_DIR,
    prompt_path: Path = DEFAULT_PROMPT,
    glossary_path: Path = DEFAULT_GLOSSARY,
    base_url: str | None = None,
    model: str | None = None,
    limit: int | None = None,
    batch_size: int = 4,
    retries: int = 2,
    timeout: float = 120.0,
) -> dict[str, Any]:
    en_rows = read_csv(en_csv)
    id_rows = read_csv(id_csv)
    jobs = build_translation_jobs(en_rows, id_rows, missing_only=True, limit=limit)
    if output is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output = draft_dir / f"{stamp}_multitext.csv"
    cache_path = output.with_suffix(".cache.jsonl")
    cached = _read_cache(cache_path)
    prompt = prompt_path.read_text(encoding="utf-8")
    glossary = _load_glossary(glossary_path)
    base_url = (base_url or os.getenv("LMSTUDIO_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    model = model or os.getenv("LMSTUDIO_MODEL") or DEFAULT_MODEL

    rows: list[dict[str, str]] = []
    processed = failed = skipped = 0
    with httpx.Client(timeout=timeout) as client:
        for batch in _chunks(jobs, batch_size):
            missing = [job for job in batch if job.row_id not in cached]
            if missing:
                batch_result = _translate_batch_with_retries(
                    client=client,
                    base_url=base_url,
                    model=model,
                    prompt=prompt,
                    glossary=glossary,
                    jobs=missing,
                    retries=retries,
                )
                for row_id, suggestion in batch_result.items():
                    cached[row_id] = suggestion
                    _append_cache(cache_path, {"id": row_id, "suggestion": suggestion})

            for job in batch:
                suggestion = cached.get(job.row_id, "")
                issues = validate_translation(job.source, suggestion)
                status = "draft" if not issues else "needs_fix"
                if issues:
                    failed += 1
                else:
                    processed += 1
                rows.append(
                    {
                        "Id": job.row_id,
                        "source": job.source,
                        "current_id": job.current_id,
                        "suggestion": suggestion,
                        "status": status,
                        "issues": "; ".join(issues),
                    }
                )
    write_csv(output, rows, DRAFT_COLUMNS)
    return {"output": str(output), "processed": processed, "failed": failed, "skipped": skipped}


def apply_draft(
    draft_csv: Path,
    id_csv: Path = DEFAULT_ID_CSV,
    output_csv: Path | None = None,
    *,
    dry_run: bool = True,
) -> dict[str, int | str]:
    id_rows = read_csv(id_csv)
    with draft_csv.open(newline="", encoding="utf-8-sig") as f:
        draft_rows = list(csv.DictReader(f))
    approved = {
        row["Id"]: row["suggestion"]
        for row in draft_rows
        if row.get("status", "").strip().lower() == "approved" and row.get("suggestion", "")
    }
    updated = 0
    for row in id_rows:
        if row["Id"] in approved and row.get("Content", "") != approved[row["Id"]]:
            row["Content"] = approved[row["Id"]]
            updated += 1
    target = output_csv or id_csv
    if not dry_run:
        write_csv(target, id_rows, list(id_rows[0].keys()) if id_rows else ["Id", "Content", "RedirectDbIndex"])
    return {"approved": len(approved), "updated": updated, "output": str(target), "dry_run": int(dry_run)}


def qa_draft(draft_csv: Path) -> dict[str, int]:
    with draft_csv.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    failed = 0
    for row in rows:
        issues = validate_translation(row.get("source", ""), row.get("suggestion", ""))
        if issues:
            failed += 1
            row["issues"] = "; ".join(issues)
            if row.get("status") == "draft":
                row["status"] = "needs_fix"
    write_csv(draft_csv, rows, DRAFT_COLUMNS)
    return {"checked": len(rows), "failed": failed}


def _translate_batch_with_retries(
    *,
    client: httpx.Client,
    base_url: str,
    model: str,
    prompt: str,
    glossary: str,
    jobs: list[TranslationJob],
    retries: int,
) -> dict[str, str]:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            payload = _request_payload(model, prompt, glossary, jobs)
            response = client.post(f"{base_url}/chat/completions", json=payload)
            response.raise_for_status()
            translations = extract_translations(response.json())
            _assert_batch_valid(jobs, translations)
            return translations
        except Exception as exc:  # retry network, JSON, and validation failures
            last_error = exc
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"LM Studio translation failed: {last_error}") from last_error


def _request_payload(model: str, prompt: str, glossary: str, jobs: list[TranslationJob]) -> dict[str, Any]:
    items = [
        {
            "id": job.row_id,
            "source": job.source,
            "current_id": job.current_id,
            "translation_memory": job.memory_suggestion,
        }
        for job in jobs
    ]
    user_content = {
        "glossary": glossary,
        "items": items,
        "output_schema": {"translations": [{"id": "string", "translation": "string"}]},
    }
    return {
        "model": model,
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(user_content, ensure_ascii=False)},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "translation_batch",
                "schema": {
                    "type": "object",
                    "properties": {
                        "translations": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "translation": {"type": "string"},
                                },
                                "required": ["id", "translation"],
                            },
                        }
                    },
                    "required": ["translations"],
                },
            },
        },
    }


def _assert_batch_valid(jobs: list[TranslationJob], translations: dict[str, str]) -> None:
    missing_ids = [job.row_id for job in jobs if job.row_id not in translations]
    if missing_ids:
        raise ValueError(f"missing translations for ids: {', '.join(missing_ids)}")
    failures: list[str] = []
    for job in jobs:
        issues = validate_translation(job.source, translations[job.row_id])
        if issues:
            failures.append(f"{job.row_id}: {'; '.join(issues)}")
    if failures:
        raise ValueError("validation failed: " + " | ".join(failures))


def _build_translation_memory(en_rows: list[dict[str, str]], id_rows: list[dict[str, str]]) -> dict[str, str]:
    id_by_key = {row["Id"]: row for row in id_rows}
    examples: dict[str, list[str]] = defaultdict(list)
    for row in en_rows:
        source = row.get("Content", "")
        translated = id_by_key.get(row["Id"], {}).get("Content", "")
        if source and translated and translated != source:
            examples[source].append(translated)
    return {source: values[0] for source, values in examples.items()}


def _load_glossary(path: Path) -> str:
    if not path.exists():
        return ""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def _read_cache(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    cache: dict[str, str] = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            item = json.loads(line)
            cache[str(item["id"])] = str(item["suggestion"])
    return cache


def _append_cache(path: Path, item: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")


def _loads_json_object(content: str) -> dict[str, Any]:
    content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _chunks(items: list[TranslationJob], size: int) -> list[list[TranslationJob]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _newline_count(value: str) -> int:
    return value.count("\n") + value.count("\\n")
