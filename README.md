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

Gunakan Python 3.10 atau lebih baru dari terminal Codespaces, Dev Container, atau lokal. Pipeline hanya memakai standard library Python, sehingga tidak memerlukan instalasi paket tambahan. Jalankan dari root repository; folder output dibuat otomatis:

```bash
python -m src.run_pipeline --require-live-api
```

Mode live tidak pernah beralih diam-diam ke fixture. Gangguan koneksi dan HTTP 429 dicoba maksimal tiga kali; HTTP error selain 429 langsung menghentikan proses. Jika pengambilan gagal, pipeline tidak menerbitkan versi processed baru.

Untuk pengujian lokal tanpa jaringan:

```bash
python -m src.run_pipeline --offline --output-root tmp/offline-data
```

Fixture offline hanya untuk pengujian dan ditandai dengan `catalog_source: offline_fixture` pada manifest.

## Implementasi LK-04

Nama dan struktur modul mengikuti rancangan LK-03:

| Tahap | Modul | Tanggung jawab |
|---|---|---|
| Ingestion | `src/ingest.py` | Mengambil katalog, statistik, dan chapter melalui API dengan `urllib.request`; menyimpan raw JSON. |
| Prapemrosesan | `src/validate.py` | Memvalidasi record, menyeragamkan kolom, serta menghapus duplikasi chapter lintas bahasa dan grup. |
| Transformasi | `src/transform.py` | Menggabungkan snapshot harian dan riwayat chapter, lalu menghitung fitur temporal ketika histori mencukupi. |
| Eksekusi otomatis | `src/run_pipeline.py` | Menjalankan ingestion dan prapemrosesan berurutan dengan konfigurasi yang sama. |

Data yang diolah berupa metadata numerik dan waktu. Tokenization dan stopword removal tidak diperlukan. Rating, jumlah vote, tahun, dan informasi chapter yang tidak tersedia tetap kosong; nilai tersebut tidak diisi angka nol atau dibuat-buat. `comments` yang kosong dipetakan menjadi `replies_count = 0`. Record yang melanggar aturan validasi dipisahkan ke karantina pada batch yang lolos quality gate. Batch ditolak jika tidak ada manga valid atau rasio record invalid melebihi 5 persen.

### Simulasi pengambilan berulang

Jalankan perintah berikut dua kali pada tanggal yang sama:

```bash
python -m src.run_pipeline --require-live-api --output-root tmp/lk04-live
python -m src.run_pipeline --require-live-api --output-root tmp/lk04-live
```

Setiap eksekusi membuat tiga raw JSON baru dengan pola:

```text
raw/{catalog,statistics,chapters}/YYYY-MM-DD/{jenis}-HHMMSS.ffffffZ-{uuid}.json
```

Waktu menggunakan UTC. UUID membedakan run sekalipun pembacaan jamnya identik. `run_id` yang sama tercatat pada ketiga raw JSON dan manifest batch. Penulisan memakai file sementara sebelum dipindahkan secara atomik. File raw lama, termasuk format lama seperti `catalog.json`, tetap dipertahankan.

Setelah dua run berhasil pada direktori output baru, terdapat enam raw JSON. Pada tabel interim, pasangan `snapshot_date` dan `manga_id` tetap unik: observasi tanggal yang sama diperbarui oleh batch valid terbaru, sedangkan observasi tanggal sebelumnya tetap tersimpan. Riwayat chapter digabung dan dideduplikasi. Manifest dan quality report pada direktori output menunjukkan batch terbaru.

Untuk simulasi tanpa jaringan, ganti `--require-live-api` dengan `--offline` dan gunakan output terpisah seperti `tmp/lk04-offline`. Folder `tmp/` tidak dilacak Git agar fixture tidak tercampur dengan sampel live yang dikumpulkan.

Cuplikan raw dari ingestion live 26 September 2026 disertakan di `data/raw/`; asal commit dan cara pemilihannya dijelaskan dalam [catatan sampel](data/raw/README.md). Sampel ini tidak otomatis dimasukkan ke riwayat harian: transformasi membaca file yang ditunjuk manifest ingestion saat berjalan, bukan seluruh JSON di `data/raw/`.

### Kesiapan data untuk continual learning

Prapemrosesan sudah menghasilkan snapshot harian dan riwayat chapter meskipun `manga_trend_features.csv` masih berisi header. Satu baris supervised baru ditulis jika judul yang sama memiliki snapshot `t-7`, `t`, dan `t+7`. Karena itu, data awal memerlukan rentang minimal 14 hari antara snapshot pertama dan terakhir. Pipeline ini menyiapkan data untuk pelatihan berikutnya; pelatihan ulang model belum dijalankan oleh workflow ingestion.

## Hasil utama

- `data/raw/{catalog,statistics,chapters}/YYYY-MM-DD/`: respons API asli dengan nama file unik per run;
- `data/interim/manga_daily_snapshots.csv`: satu observasi per manga per hari;
- `data/interim/chapter_history.csv`: riwayat chapter unik lintas bahasa;
- `data/processed/manga_trend_features.csv`: fitur yang telah memiliki histori dan target;
- `data/quarantine/`: record yang gagal validasi;
- `data/metadata/quality_report.json`: metrik kualitas batch;
- `data/metadata/manifest.json`: ID run, waktu pengambilan, path raw, sumber, versi schema, jumlah record, dan checksum processed.

## Otomasi

Workflow `.github/workflows/ingest.yml` berjalan setiap hari pukul 02.10 UTC atau 09.10 WIB. Histori dipulihkan dan disimpan pada branch `data-snapshots`, sedangkan hasil setiap run juga tersedia sebagai GitHub Actions artifact selama 14 hari. Branch data hanya dibuat oleh workflow setelah perubahan kode digabung dan workflow dijalankan.

## Pengujian

```bash
python -m unittest discover -s tests -v
```

Pengujian memastikan kegagalan jaringan tidak memakai fixture, filter katalog hanya memilih bahasa asli Korea, dan duplikasi chapter lintas bahasa mempertahankan waktu ketersediaan paling awal. Uji pengambilan berulang juga memeriksa raw lama tidak berubah meskipun waktu pengambilan sama, snapshot tanggal sebelumnya tetap tersedia, serta satu judul tidak berulang pada tanggal yang sama di interim.

Proyek ini menggunakan API publik MangaDex untuk keperluan mata kuliah, tidak menampilkan isi chapter, dan tidak dimonetisasi. MangaDex tetap harus dicantumkan sebagai sumber data.
