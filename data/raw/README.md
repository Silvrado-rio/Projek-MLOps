# Sampel raw MangaDex untuk LK-04

Tiga file `*-sample.json` merupakan cuplikan data live untuk judul **Save the Earth!** (`0017ab16-4f1f-452a-99d2-5f7dec895a67`), diambil dari snapshot 26 September 2026 pukul 07.35.41 UTC (14.35.41 WIB).

Sumber: [commit 5e56130 pada branch data-snapshots](https://github.com/Silvrado-rio/Projek-MLOps/tree/5e56130ea03ecd7e7d124eeade3e793c2ca4f840/data/raw).

| File | Isi |
|---|---|
| `catalog/2026-09-26/catalog-sample.json` | Satu record katalog manhwa. |
| `statistics/2026-09-26/statistics-sample.json` | Statistik untuk ID manhwa yang sama. |
| `chapters/2026-09-26/chapters-sample.json` | Record chapter milik manhwa tersebut yang tersedia dalam batch sumber. |

Record individual dipertahankan sesuai sumber, termasuk nilai null, ID, waktu, dan bahasa. Hanya record manga lain serta metadata pagination dan salinan `pages` gabungan yang tidak disertakan. Field tambahan `sample` mencatat commit, path asal, ID manga, dan metode seleksi. `source: mangadex_api` serta `fetched_at` berasal dari pengambilan asli; sampel ini bukan hasil request baru atau fixture sintetis.

Sampel sengaja kecil agar dapat diperiksa bersama kode pada branch eksperimen. Histori lengkap tetap berada di branch `data-snapshots`. File sampel menggunakan akhiran `-sample.json`, sedangkan ingestion baru menggunakan nama berisi waktu UTC dan UUID.

## Memverifikasi sampel

Jalankan dari root repository:

```bash
python -m unittest tests.test_pipeline.PipelineTest.test_committed_live_sample_can_be_preprocessed_repeatedly -v
```

Pengujian memproses ketiga file dua kali di direktori sementara tanpa jaringan. Raw tidak berubah, snapshot harian tidak berlipat, dan checksum processed tetap sama. Nilai `rating_votes` yang tidak disediakan API tetap kosong. File fitur belum memiliki baris karena sampel hanya mencakup satu tanggal.

Pengujian pengambilan berulang dengan perubahan statistik dijalankan oleh seluruh suite:

```bash
python -m unittest discover -s tests -v
```

Percobaan ingestion live baru dari mesin lokal saat pengerjaan LK-04 gagal karena `CERTIFICATE_VERIFY_FAILED` dengan hostname mismatch. Karena itu, sampel memakai hasil live terdahulu yang dapat ditelusuri. Tidak ada penonaktifan verifikasi TLS atau penggantian data live dengan fixture.
