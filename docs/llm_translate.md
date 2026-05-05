# Wiki LLM Translator

Panduan ini menjelaskan workflow menerjemahkan `TextGame/en/MultiText_EN.csv` ke draft Bahasa Indonesia memakai LM Studio dan tool Python `tools.llm_translate`.

## Tujuan

`llm_translate` tidak langsung menimpa `TextGame/id/MultiText_ID.csv`. Tool ini membuat draft CSV untuk dicek manusia dulu. Setelah reviewer menandai baris sebagai `approved`, baris itu baru bisa digabung ke CSV Bahasa Indonesia.

Workflow utama:

1. Update prompt dan glossarium.
2. Generate draft terjemahan dengan LLM lokal.
3. Review draft CSV.
4. Jalankan QA.
5. Apply hanya row berstatus `approved`.

## Setup

Install dependency Python dengan `uv`:

```bash
uv sync
```

Pastikan LM Studio server aktif dan model sudah loaded.

Default yang dipakai tool:

```text
Base URL: http://127.0.0.1:1234/v1
Model: google/gemma-4-e4b
```

Cek server:

```bash
curl http://127.0.0.1:1234/v1/models
```

Kalau endpoint atau model berbeda, pakai env:

```bash
export LMSTUDIO_BASE_URL="http://127.0.0.1:1234/v1"
export LMSTUDIO_MODEL="google/gemma-4-e4b"
```

Atau kirim langsung lewat CLI:

```bash
uv run python -m tools.llm_translate translate \
  --base-url http://127.0.0.1:1234/v1 \
  --model google/gemma-4-e4b
```

## File Penting

| File | Fungsi |
|---|---|
| `tools/llm_translate/prompt.md` | Custom prompt untuk model lokal. |
| `tools/llm_translate/glossary.yaml` | Glossarium istilah, gaya bahasa, dan aturan preserve. |
| `TextGame/en/MultiText_EN.csv` | Source English. |
| `TextGame/id/MultiText_ID.csv` | Target Bahasa Indonesia yang sudah direview. |
| `llm_drafts/*.csv` | Output draft untuk human review. |
| `llm_drafts/*.cache.jsonl` | Cache resume request/response per `Id`. |

`llm_drafts/` di-ignore oleh git, jadi draft lokal tidak ikut commit.

## Generate Draft

Mulai dari batch kecil:

```bash
uv run python -m tools.llm_translate translate --limit 10
```

Output akan muncul seperti:

```text
output: llm_drafts/20260505_132800_multitext.csv
processed: 10
failed: 0
skipped: 0
```

Kalau mau tentukan output sendiri:

```bash
uv run python -m tools.llm_translate translate \
  --limit 100 \
  --batch-size 4 \
  --output llm_drafts/batch_quest.csv
```

Catatan:

- Tool hanya memproses row yang masih kosong atau masih sama dengan English.
- Row kosong di source akan dilewati.
- Kalau proses berhenti, jalankan command yang sama dengan `--output` yang sama agar cache dipakai lagi.

## Review Draft

Buka draft CSV di spreadsheet editor.

Kolom draft:

| Kolom | Arti |
|---|---|
| `Id` | ID teks game. |
| `source` | Teks English. |
| `current_id` | Isi Bahasa Indonesia saat ini. |
| `suggestion` | Usulan dari LLM. |
| `status` | Status review. |
| `issues` | Masalah hasil validator. |

Status yang dipakai:

| Status | Arti |
|---|---|
| `draft` | Usulan valid secara teknis, belum direview. |
| `needs_fix` | Ada masalah placeholder/tag/newline atau output kosong. |
| `approved` | Reviewer setuju untuk apply ke CSV ID. |

Untuk approve:

1. Edit `suggestion` kalau perlu.
2. Ubah `status` menjadi `approved`.
3. Simpan CSV.

## QA Draft

Jalankan QA sebelum apply:

```bash
uv run python -m tools.llm_translate qa llm_drafts/batch_quest.csv
```

QA akan mengecek:

- Tag seperti `<color=Highlight>` dan `</color>`.
- Placeholder seperti `{Name}`, `%s`, `%d`.
- Jumlah newline asli dan literal `\n`.
- Source kosong harus tetap kosong.

Jika ada masalah, kolom `issues` akan diisi dan status `draft` berubah menjadi `needs_fix`.

## Apply Draft

Default `apply` adalah dry-run:

```bash
uv run python -m tools.llm_translate apply llm_drafts/batch_quest.csv
```

Contoh output:

```text
approved: 25
updated: 25
output: TextGame/id/MultiText_ID.csv
dry_run: 1
```

Untuk benar-benar menulis ke `TextGame/id/MultiText_ID.csv`:

```bash
uv run python -m tools.llm_translate apply llm_drafts/batch_quest.csv --write
```

Untuk menulis ke file lain dulu:

```bash
uv run python -m tools.llm_translate apply llm_drafts/batch_quest.csv \
  --output /tmp/MultiText_ID.preview.csv \
  --write
```

Hanya row dengan `status=approved` dan `suggestion` tidak kosong yang akan diaplikasikan.

## Prompt dan Glossarium

Edit prompt:

```bash
$EDITOR tools/llm_translate/prompt.md
```

Edit glossarium:

```bash
$EDITOR tools/llm_translate/glossary.yaml
```

Contoh entry glossarium:

```yaml
terms:
  - source: Resonator
    target: Resonator
    note: Core Wuthering Waves term; keep consistent.
```

Gunakan glossarium untuk:

- Proper noun.
- Istilah gameplay.
- Nada bahasa.
- Kata yang harus tetap English.

## Troubleshooting

### LM Studio tidak tersambung

Gejala:

```text
Could not connect to server
```

Cek:

```bash
curl http://127.0.0.1:1234/v1/models
```

Pastikan server LM Studio aktif dan port benar.

### Model belum loaded

Gejala:

```text
No models loaded
```

Load model `google/gemma-4-e4b` di LM Studio Developer page, lalu ulangi command.

### Banyak `needs_fix`

Kemungkinan:

- Model menghapus tag.
- Prompt kurang tegas.
- Batch terlalu besar.

Coba:

```bash
uv run python -m tools.llm_translate translate --limit 20 --batch-size 1
```

Lalu perbaiki `prompt.md` dan `glossary.yaml`.

### Output JSON gagal

Tool memakai `json_schema` karena LM Studio lokal ini menerima `json_schema` atau `text`. Jika model tetap mengeluarkan JSON rusak, turunkan `--batch-size` dan naikkan `--retries`.

```bash
uv run python -m tools.llm_translate translate --batch-size 1 --retries 4
```

## Workflow Rekomendasi

Untuk batch aman:

```bash
uv sync
uv run python -m tools.llm_translate translate --limit 50 --batch-size 2
uv run python -m tools.llm_translate qa llm_drafts/<draft>.csv
uv run python -m tools.llm_translate apply llm_drafts/<draft>.csv
uv run python -m tools.llm_translate apply llm_drafts/<draft>.csv --write
uv run pytest -q
```

Commit hanya setelah:

- Draft sudah direview.
- QA draft tidak punya masalah penting.
- `TextGame/id/MultiText_ID.csv` berisi perubahan approved saja.
- `uv run pytest -q` pass.
