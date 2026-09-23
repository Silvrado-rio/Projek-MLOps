# Sistem Rekomendasi Manhwa Berbasis Tren Sentimen Komentar

Repositori ini berisi pipeline data untuk proyek MLOps yang memberi peringkat kandidat rekomendasi berdasarkan perubahan sentimen komentar pada setiap chapter. MangaDex menyediakan metadata chapter, sedangkan komentar pada tahap perkuliahan ini merupakan data sintetis dan selalu ditandai dengan `synthetic: true`.

## Alur data

```text
MangaDex API + simulator komentar
              ↓
        data/raw (JSON/JSONL)
              ↓
      validasi dan cleaning
              ↓
       data/interim (CSV)
              ↓
 feature engineering per chapter
              ↓
      data/processed (CSV)
```

Pipeline membentuk fitur `comment_count`, rasio sentimen, `sentiment_score`, `trend_delta`, dan `recency_weight`. Distribusi komentar berubah mulai batch kelima untuk mensimulasikan data drift.

## Menjalankan pipeline

```bash
python -m src.run_pipeline
```

Untuk menjalankan tanpa internet:

```bash
python -m src.run_pipeline --offline
```

Untuk mewajibkan metadata asli MangaDex dan membatalkan pipeline jika API tidak dapat diakses:

```bash
python -m src.run_pipeline --require-live-api
```

Periksa `catalog_source` pada `data/metadata/manifest.json`: nilai `mangadex_api`
menandakan metadata live, sedangkan `fallback_catalog` menandakan katalog demo. Komentar
pada tahap perkuliahan tetap sintetis pada kedua mode.

Hasil utama berada di:

- `data/raw/`: respons metadata dan event komentar mentah;
- `data/interim/comments_clean.csv`: komentar yang lolos validasi;
- `data/processed/chapter_sentiment_features.csv`: fitur siap digunakan model;
- `data/metadata/quality_report.json`: jumlah record valid, ditolak, dan duplikat;
- `data/metadata/manifest.json`: sumber, lokasi artefak, dan checksum dataset.

Workflow `.github/workflows/ingest.yml` menjalankan pipeline setiap hari pukul 02.10 UTC atau 09.10 WIB dengan mode `--require-live-api`. Workflow akan gagal jika metadata asli MangaDex tidak dapat diambil sehingga fallback tidak pernah disimpan sebagai data live. Pada tahap simulasi, setiap eksekusi membentuk jendela tujuh batch agar perubahan distribusi dapat diamati, lalu menyimpan hasilnya sebagai GitHub Actions artifact. Belum ada deployment model pada tahap ini.

## Struktur penting

```text
configs/pipeline.json       parameter API dan simulasi
src/ingest.py               extract metadata dan membentuk batch komentar
src/validate.py             validasi, deduplikasi, dan cleaning
src/transform.py            agregasi fitur dan manifest
src/run_pipeline.py         entry point pipeline
data/                       raw, interim, processed, dan metadata
```
