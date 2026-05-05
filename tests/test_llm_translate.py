import csv
import json
import tempfile
import unittest
from pathlib import Path

import httpx

from tools import llm_translate


class ValidationTests(unittest.TestCase):
    def test_validator_reports_missing_markup_and_placeholders(self):
        issues = llm_translate.validate_translation(
            "Use <color=Highlight>{Name}</color>\\nNow %s",
            "Gunakan {Name} sekarang",
        )

        self.assertIn("missing tag: <color=Highlight>", issues)
        self.assertIn("missing tag: </color>", issues)
        self.assertIn("newline count mismatch", issues)
        self.assertIn("missing placeholder: %s", issues)

    def test_empty_source_requires_empty_suggestion(self):
        self.assertEqual(llm_translate.validate_translation("", ""), [])
        self.assertIn("empty source should stay empty", llm_translate.validate_translation("", "Isi"))


class CsvWorkflowTests(unittest.TestCase):
    def test_build_translation_jobs_uses_missing_only_and_translation_memory(self):
        en_rows = [
            {"Id": "A", "Content": "Talk to Shilang", "RedirectDbIndex": "0"},
            {"Id": "B", "Content": "Known", "RedirectDbIndex": "0"},
            {"Id": "C", "Content": "Known", "RedirectDbIndex": "0"},
            {"Id": "D", "Content": "", "RedirectDbIndex": "0"},
        ]
        id_rows = [
            {"Id": "A", "Content": "", "RedirectDbIndex": "0"},
            {"Id": "B", "Content": "Known", "RedirectDbIndex": "0"},
            {"Id": "C", "Content": "Dikenal", "RedirectDbIndex": "0"},
            {"Id": "D", "Content": "", "RedirectDbIndex": "0"},
        ]

        jobs = llm_translate.build_translation_jobs(en_rows, id_rows, missing_only=True)

        self.assertEqual([job.row_id for job in jobs], ["A", "B"])
        self.assertEqual(jobs[1].memory_suggestion, "Dikenal")

    def test_apply_draft_only_updates_approved_rows_and_preserves_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            id_csv = tmp_path / "id.csv"
            draft_csv = tmp_path / "draft.csv"
            output_csv = tmp_path / "out.csv"
            id_csv.write_text(
                "Id,Content,RedirectDbIndex\nA,,0\nB,Lama,0\n",
                encoding="utf-8",
            )
            draft_csv.write_text(
                "Id,source,current_id,suggestion,status,issues\n"
                "A,Hello,,Halo,approved,\n"
                "B,Old,Lama,Baru,draft,\n",
                encoding="utf-8",
            )

            result = llm_translate.apply_draft(draft_csv, id_csv, output_csv, dry_run=False)

            self.assertEqual(result["updated"], 1)
            with output_csv.open(newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(rows[0]["Content"], "Halo")
            self.assertEqual(rows[1]["Content"], "Lama")
            self.assertEqual(list(rows[0].keys()), ["Id", "Content", "RedirectDbIndex"])


class LmStudioTests(unittest.TestCase):
    def test_request_payload_uses_lm_studio_supported_json_schema(self):
        payload = llm_translate.core._request_payload(
            "google/gemma-4-e4b",
            "Translate.",
            "",
            [llm_translate.TranslationJob("A", "Hello", "")],
        )

        self.assertEqual(payload["response_format"]["type"], "json_schema")
        self.assertIn("json_schema", payload["response_format"])

    def test_parse_llm_json_translation_from_chat_response(self):
        payload = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"translations": [{"id": "A", "translation": "Halo"}]}
                        )
                    }
                }
            ]
        }

        self.assertEqual(llm_translate.extract_translations(payload), {"A": "Halo"})

    def test_translate_batch_retries_invalid_json_then_succeeds(self):
        calls = []

        def handler(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(
                    200,
                    json={"choices": [{"message": {"content": "not json"}}]},
                )
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {"translations": [{"id": "A", "translation": "Halo"}]}
                                )
                            }
                        }
                    ]
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        result = llm_translate.core._translate_batch_with_retries(
            client=client,
            base_url="http://lmstudio/v1",
            model="google/gemma-4-e4b",
            prompt="Translate.",
            glossary="",
            jobs=[llm_translate.TranslationJob("A", "Hello", "")],
            retries=1,
        )

        self.assertEqual(result, {"A": "Halo"})
        self.assertEqual(len(calls), 2)

    def test_translate_batch_retries_broken_markup_and_then_fails(self):
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "message": {
                                    "content": json.dumps(
                                        {
                                            "translations": [
                                                {"id": "A", "translation": "Sorotan"}
                                            ]
                                        }
                                    )
                                }
                            }
                        ]
                    },
                )
            )
        )

        with self.assertRaisesRegex(RuntimeError, "validation failed"):
            llm_translate.core._translate_batch_with_retries(
                client=client,
                base_url="http://lmstudio/v1",
                model="google/gemma-4-e4b",
                prompt="Translate.",
                glossary="",
                jobs=[llm_translate.TranslationJob("A", "<color=Highlight>Highlight</color>", "")],
                retries=1,
            )

    def test_translate_batch_wraps_timeout_errors(self):
        def handler(request):
            raise httpx.TimeoutException("boom", request=request)

        client = httpx.Client(transport=httpx.MockTransport(handler))

        with self.assertRaisesRegex(RuntimeError, "LM Studio translation failed"):
            llm_translate.core._translate_batch_with_retries(
                client=client,
                base_url="http://lmstudio/v1",
                model="google/gemma-4-e4b",
                prompt="Translate.",
                glossary="",
                jobs=[llm_translate.TranslationJob("A", "Hello", "")],
                retries=0,
            )


if __name__ == "__main__":
    unittest.main()
