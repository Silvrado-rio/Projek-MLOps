# Sistem Pemeringkatan Manhwa Berbasis Tren Aktivitas MangaDex

Repositori ini berisi pipeline batch harian untuk memeringkat manhwa berdasarkan perubahan aktivitas komunitas dan pembaruan chapter di MangaDex. Katalog dibatasi pada karya dengan bahasa asli Korea (`originalLanguage[]=ko`). Bahasa terjemahan tidak menjadi filter karena pipeline hanya memakai metadata numerik, bukan teks chapter atau komentar.

## Data yang diambil

- `GET /manga`: identitas, judul, status, tahun, tag, dan content rating;
- `GET /statistics/manga`: follows, rating, jumlah vote jika tersedia, dan `repliesCount`;
- `GET /chapter`: volume, nomor chapter, bahasa terjemahan, `publishAt`, dan jumlah halaman.

`repliesCount` hanya dipakai sebagai volume aktivitas komunitas, bukan analisis sentimen. Chapter dari bahasa atau grup berbeda dideduplikasi berdasarkan manga, volume, dan nomor chapter. `publishAt` paling awal dipertahankan sebagai waktu pertama kali chapter tersedia di MangaDex; nilai ini bukan tanggal rilis resmi di Korea.

## Alur data

```text
MangaDex API
    ↓
raw JSON bertanggal
    ↓
validasi dan deduplikasi
    ↓
snapshot harian + riwayat chapter
    ↓
fitur temporal dan target t+7
```

Pipeline menghasilkan fitur pertumbuhan follows, perubahan `repliesCount` dan rating, jumlah chapter unik tujuh hari, recency, serta rata-rata interval pembaruan. Dataset supervised baru berisi baris setelah snapshot t-7 dan t+7 tersedia.

## Menjalankan pipeline

Jalankan dari root repository:

```bash
python -m src.run_pipeline --require-live-api
```

Mode live tidak pernah beralih diam-diam ke fixture. Jika API gagal setelah tiga percobaan, pipeline berhenti dan tidak menerbitkan versi processed baru.

Untuk pengujian lokal tanpa jaringan:

```bash
python -m src.run_pipeline --offline --output-root tmp/offline-data
```

Fixture offline hanya untuk pengujian dan ditandai dengan `catalog_source: offline_fixture` pada manifest.

## Hasil utama

- `data/raw/{catalog,statistics,chapters}/YYYY-MM-DD/`: respons API asli;
- `data/interim/manga_daily_snapshots.csv`: satu observasi per manga per hari;
- `data/interim/chapter_history.csv`: riwayat chapter unik lintas bahasa;
- `data/processed/manga_trend_features.csv`: fitur yang telah memiliki histori dan target;
- `data/quarantine/`: record yang gagal validasi;
- `data/metadata/quality_report.json`: metrik kualitas batch;
- `data/metadata/manifest.json`: sumber, versi schema, jumlah record, dan checksum.

## Otomasi

Workflow `.github/workflows/ingest.yml` berjalan setiap hari pukul 02.10 UTC atau 09.10 WIB. Histori dipulihkan dan disimpan pada branch `data-snapshots`, sedangkan hasil setiap run juga tersedia sebagai GitHub Actions artifact selama 14 hari. Branch data hanya dibuat oleh workflow setelah perubahan kode digabung dan workflow dijalankan.

## Pengujian

```bash
python -m unittest discover -s tests -v
```

Pengujian memastikan kegagalan jaringan tidak memakai fixture, filter katalog hanya memilih bahasa asli Korea, dan duplikasi chapter lintas bahasa mempertahankan waktu ketersediaan paling awal.

Proyek ini menggunakan API publik MangaDex untuk keperluan mata kuliah, tidak menampilkan isi chapter, dan tidak dimonetisasi. MangaDex tetap harus dicantumkan sebagai sumber data.
