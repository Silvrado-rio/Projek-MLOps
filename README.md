# Sistem Pemeringkatan Manhwa Berbasis Tren Aktivitas MangaDex

Repositori ini berisi pipeline batch harian untuk memeringkat manhwa berdasarkan perubahan aktivitas komunitas dan pembaruan chapter di MangaDex. Katalog dibatasi pada karya dengan bahasa asli Korea (`originalLanguage[]=ko`). Bahasa terjemahan tidak menjadi filter karena pipeline hanya memakai metadata numerik, bukan teks chapter atau komentar.

## Data yang diambil

- `GET /manga`: identitas, judul, status, tahun, tag, dan content rating;
- `GET /statistics/manga`: follows, rating, jumlah vote jika tersedia, dan `repliesCount`;
- `GET /chapter`: volume, nomor chapter, bahasa terjemahan, `publishAt`, dan jumlah halaman.

`repliesCount` hanya dipakai sebagai volume aktivitas komunitas, bukan analisis sentimen. Chapter dari bahasa atau grup berbeda dideduplikasi berdasarkan manga, volume, dan nomor chapter. `publishAt` paling awal dipertahankan sebagai waktu pertama kali chapter tersedia di MangaDex; nilai ini bukan tanggal rilis resmi di Korea.

### Cohort pengamatan tetap

Target pengamatan adalah **200 ID tetap**. Konfigurasi memuat 142 ID yang sudah muncul dalam riwayat sampai 9 Oktober 2026, termasuk 50 ID dari konfigurasi awal. Run live pertama melengkapi daftar tersebut dengan 58 ID tambahan dari katalog Korea ber-rating `safe`/`suggestive`, diurutkan menurut pembaruan terbaru. Pencarian dan pengambilan katalog dibagi menjadi halaman maksimal 100 record.

Setelah batch pertama berhasil, daftar lengkap disimpan di `data/metadata/tracked_manga_ids.json` dan dilacak DVC. Run berikutnya memakai daftar tersimpan, sehingga urutan pembaruan katalog tidak mengganti anggota sampel. ID katalog atau statistik yang hilang menghentikan pengambilan batch; pipeline tidak mengganti ID tersebut dengan judul baru. Perubahan ukuran cohort harus dilakukan secara sengaja bersama migrasi daftar tersimpan.

Angka 200 merupakan batas praktis pengumpulan, bukan hasil perhitungan keterwakilan populasi. Metadata chapter tetap dibatasi 20 record terbaru per ID per run. ID tambahan belum memiliki histori dua minggu; baris supervised hanya tersedia setelah ID yang sama memiliki snapshot pada `t-7`, `t`, dan `t+7`.

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

Gunakan Python 3.10 atau lebih baru dari terminal Codespaces, Dev Container, atau lokal. Ingestion dan transformasi memakai standard library; DVC dengan backend Google Drive diperlukan untuk memulihkan dan menyimpan versi dataset. Jalankan dari root repository:

```bash
python -m pip install -r requirements.txt
dvc pull
```

`dvc pull` memerlukan konfigurasi OAuth lokal atau secrets pada Actions, seperti dijelaskan di bawah. Setelah histori dipulihkan:

```bash
python -m src.run_pipeline --require-live-api
```

Mode live tidak pernah beralih diam-diam ke fixture. Gangguan koneksi dan HTTP 429 dicoba maksimal tiga kali; HTTP error selain 429 langsung menghentikan proses. Jika pengambilan gagal, pipeline tidak menerbitkan versi processed baru.

Untuk pengujian lokal tanpa jaringan:

```bash
python -m src.run_pipeline --offline --output-root tmp/offline-data
```

Fixture offline hanya untuk pengujian dan ditandai dengan `catalog_source: offline_fixture` pada manifest.

## Modul pipeline

Pipeline dibagi menjadi modul pengambilan, validasi, transformasi, dan eksekusi:

| Tahap | Modul | Tanggung jawab |
|---|---|---|
| Ingestion | `src/ingest.py` | Mengambil katalog, statistik, dan chapter melalui API dengan `urllib.request`; menyimpan raw JSON. |
| Prapemrosesan | `src/validate.py` | Memvalidasi record, menyeragamkan kolom, serta menghapus duplikasi chapter lintas bahasa dan grup. |
| Transformasi | `src/transform.py` | Menggabungkan snapshot harian dan riwayat chapter, lalu menghitung fitur temporal ketika histori mencukupi. |
| Eksekusi otomatis | `src/run_pipeline.py` | Menjalankan ingestion dan prapemrosesan berurutan dengan konfigurasi yang sama. |

Data yang diolah berupa metadata numerik dan waktu. Rating, jumlah vote, tahun, dan informasi chapter yang tidak tersedia tetap kosong; nilai tersebut tidak diisi angka nol atau dibuat-buat. `comments` yang kosong dipetakan menjadi `replies_count = 0`. Record yang melanggar aturan validasi dipisahkan ke karantina pada batch yang lolos quality gate. Batch ditolak jika tidak ada manga valid atau rasio record invalid melebihi 5 persen.

### Simulasi pengambilan berulang

Jalankan perintah berikut dua kali pada tanggal yang sama:

```bash
python -m src.run_pipeline --require-live-api --output-root tmp/live-data
python -m src.run_pipeline --require-live-api --output-root tmp/live-data
```

Setiap eksekusi membuat tiga raw JSON baru dengan pola:

```text
raw/{catalog,statistics,chapters}/YYYY-MM-DD/{jenis}-HHMMSS.ffffffZ-{uuid}.json
```

Waktu menggunakan UTC. UUID membedakan run sekalipun pembacaan jamnya identik. `run_id` yang sama tercatat pada ketiga raw JSON dan manifest batch. Penulisan memakai file sementara sebelum dipindahkan secara atomik. File raw lama, termasuk format lama seperti `catalog.json`, tetap dipertahankan.

Setelah dua run berhasil pada direktori output baru, terdapat enam raw JSON. Pada tabel interim, pasangan `snapshot_date` dan `manga_id` tetap unik: observasi tanggal yang sama diperbarui oleh batch valid terbaru, sedangkan observasi tanggal sebelumnya tetap tersimpan. Riwayat chapter digabung dan dideduplikasi. Manifest dan quality report pada direktori output menunjukkan batch terbaru.

Untuk simulasi tanpa jaringan, ganti `--require-live-api` dengan `--offline` dan gunakan output terpisah seperti `tmp/offline-data`. Folder `tmp/` tidak dilacak Git agar fixture tidak tercampur dengan sampel live yang dikumpulkan.

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

## Sampel data

Tiga file `*-sample.json` di `data/raw/` merupakan cuplikan data live untuk judul **Save the Earth!** (`0017ab16-4f1f-452a-99d2-5f7dec895a67`), diambil dari snapshot 26 September 2026 pukul 07.35.41 UTC (14.35.41 WIB).

Sumber: [commit 5e56130 pada branch data-snapshots](https://github.com/Silvrado-rio/Projek-MLOps/tree/5e56130ea03ecd7e7d124eeade3e793c2ca4f840/data/raw).

| File | Isi |
|---|---|
| `catalog-sample.json` | Satu record katalog manhwa. |
| `statistics-sample.json` | Statistik untuk ID manhwa yang sama. |
| `chapters-sample.json` | Enam record chapter milik manhwa tersebut yang tersedia dalam batch sumber. |

Record individual dipertahankan sesuai sumber, termasuk nilai null, ID, waktu, dan bahasa. Hanya record manga lain serta metadata pagination dan salinan `pages` gabungan yang tidak disertakan. Field tambahan `sample` mencatat commit, path asal, ID manga, dan metode seleksi. `source: mangadex_api` serta `fetched_at` berasal dari pengambilan asli; sampel ini bukan hasil request baru atau fixture sintetis.

Sampel berukuran kecil agar mudah diperiksa dan digunakan untuk menguji prapemrosesan. File sampel dan histori lengkap sekarang dipulihkan dari remote DVC dengan `dvc pull`. Branch `data-snapshots` menyimpan pointer versi terbaru; commit lama yang memuat data langsung tetap dipertahankan. File sampel menggunakan akhiran `-sample.json`, sedangkan ingestion baru menggunakan nama berisi waktu UTC dan UUID.

Sampel tidak otomatis dimasukkan ke riwayat harian: transformasi membaca file yang ditunjuk manifest ingestion saat berjalan, bukan seluruh JSON di `data/raw/`.

## Otomasi

Workflow `.github/workflows/ingest.yml` berjalan setiap hari pukul 02.10 UTC atau 09.10 WIB. Workflow memasang DVC, memulihkan pointer terbaru dari `data-snapshots`, menjalankan `dvc pull`, menguji pipeline, mengambil observasi live, lalu menjalankan `dvc add` dan `dvc push`. Pointer baru baru di-commit ke branch data setelah upload berhasil. Eksekusi bersamaan diserialkan agar tidak menimpa versi satu sama lain.

Pada migrasi pertama, histori Git lama dipulihkan setelah baseline DVC untuk mempertahankan pengambilan yang terjadi setelah baseline dibuat. Raw dan CSV tidak ditambahkan lagi ke Git; versi selanjutnya menyimpan cache di Drive. Commit lama di branch data tidak dihapus atau ditulis ulang. Hasil dan audit setiap run tersedia sebagai GitHub Actions artifact selama 14 hari.

### Google Drive dan kredensial

Remote default `drive` menggunakan folder Google Drive yang tercantum pada `.dvc/config`. Folder tetap privat. Akun pribadi menggunakan OAuth pengguna, bukan service account tanpa kuota penyimpanan. Buat OAuth client bertipe **Desktop app** pada proyek Google Cloud dengan Drive API aktif dan scope `https://www.googleapis.com/auth/drive` serta `https://www.googleapis.com/auth/drive.appdata`.

Client ID, client secret, dan lokasi kredensial pengguna dikonfigurasi secara lokal. Contoh PowerShell setelah environment variable disiapkan di komputer sendiri:

```powershell
dvc remote modify --local drive gdrive_client_id $env:GDRIVE_CLIENT_ID
dvc remote modify --local drive gdrive_client_secret $env:GDRIVE_CLIENT_SECRET
dvc remote modify --local drive gdrive_user_credentials_file C:/path/private/gdrive-user-credentials.json
dvc push
```

Penggunaan `--local` menyimpan nilai di `.dvc/config.local`, yang diabaikan Git. File OAuth client JSON yang diunduh dari Google berbeda dari file kredensial pengguna yang dibuat setelah login browser. Jangan commit atau menampilkan kedua file tersebut dalam log atau screenshot.

Sebelum menjalankan Actions, tambahkan secrets berikut melalui **Settings → Secrets and variables → Actions**:

| Secret | Nilai |
|---|---|
| `GDRIVE_CLIENT_ID` | `client_id` dari OAuth client Desktop. |
| `GDRIVE_CLIENT_SECRET` | `client_secret` dari OAuth client Desktop. |
| `GDRIVE_CREDENTIALS_DATA` | Seluruh JSON kredensial pengguna hasil login PyDrive2/DVC; bukan JSON OAuth client. |

Status OAuth **Testing** dengan scope Drive menghasilkan refresh token yang berlaku tujuh hari. Untuk pengumpulan berkelanjutan, selesaikan pengaturan Branding, ubah Audience ke **In production**, lalu autentikasi ulang sebelum mengisi secret token. Token tetap dapat dicabut atau kedaluwarsa karena kondisi lain. Aplikasi untuk penggunaan pribadi memiliki pengecualian verifikasi; perubahan status tidak menjamin semua pemeriksaan Google dilewati.

Halaman aplikasi, privasi, dan ketentuan tersedia sebagai HTML statis pada `docs/`. Publikasikan melalui GitHub Pages sebelum memasukkan URL-nya pada Branding. Homepage harus menautkan kedua halaman kebijakan; jika Google meminta verifikasi domain, selesaikan melalui Search Console untuk domain yang dikelola pemilik proyek. Halaman HTML lokal saja belum merupakan URL publik.

Referensi: [remote Google Drive DVC](https://doc.dvc.org/user-guide/data-management/remote-storage/google-drive), [masa berlaku token OAuth](https://developers.google.com/identity/protocols/oauth2), dan [pengaturan Branding Google](https://support.google.com/cloud/answer/15549049?hl=en).

### Penambahan dan audit versi dataset

DVC sudah diinisialisasi pada repository. Versi awal berasal dari commit `a906ada11b838c75663d63d2fe133244a83e3600` di branch data, berisi 850 snapshot harian, 142 ID unik, dan 877 chapter unik. Raw, interim, processed, metadata, serta quarantine masing-masing memiliki pointer `.dvc`; cache lokal dan data aktual diabaikan Git.

Untuk menambah versi di komputer yang sudah diautentikasi:

```bash
dvc pull
python -m src.run_pipeline --require-live-api
dvc add data/raw data/interim data/processed data/metadata data/quarantine
dvc diff --json
dvc status
dvc push
git add data/*.dvc data/.gitignore
git commit -m "data: record the next observation"
```

File raw bertambah pada setiap run sukses. Snapshot tanggal yang sama untuk ID yang sama diperbarui, sehingga demonstrasi penambahan baris harian sebaiknya memakai tanggal UTC berikutnya. Penambahan observasi adalah penambahan dataset; workflow ini belum melatih ulang model.

Untuk membandingkan baseline kode dengan versi terbaru dari Actions:

```bash
git fetch origin main data-snapshots
dvc diff origin/main origin/data-snapshots --json
```

`dvc diff` membandingkan hash dan perubahan file/direktori, bukan jumlah baris CSV. Artifact run memuat `baseline.json`, `growth.json`, `diff.json`, dan `status.txt` pada folder audit: jumlah snapshot, ID unik, serta raw sebelum dan sesudah dicatat terpisah. Output terminal `dvc diff`/`dvc status` dapat dijadikan bukti tangkapan layar setelah dua versi nyata tersedia. Hash baru dan perubahan dataset tidak boleh diklaim sebelum run baru berhasil.

## Pelatihan regresi dan eksperimen MLflow

`train.py` memprediksi `target_growth_next_7d`, yaitu `log(1 + follows[t+7]) - log(1 + follows[t])`. Model awal adalah Ridge Regression dengan tiga nilai alpha: 0.1, 1, dan 10. Baseline momentum menggunakan perubahan log followers tujuh hari sebelumnya. MAE menjadi metrik pemilihan; RMSE, R2, serta NDCG@10 per tanggal dicatat sebagai metrik pendamping. NDCG memakai relevansi `max(target, 0)` dan membandingkan ranking model dengan momentum serta total followers. NDCG dirata-ratakan hanya pada tanggal dengan minimal dua judul dan setidaknya satu pertumbuhan positif; jika tidak ada tanggal yang memenuhi syarat, metrik NDCG tidak diterbitkan dan gate tidak lulus.

### Instalasi dan koneksi data

Codespace baru memasang `requirements-train.txt` melalui konfigurasi dev container. Pada Codespace yang sudah ada, jalankan dari root repository:

```bash
python -m pip install -r requirements-train.txt
python -m pip check
git fetch origin data-snapshots
```

Training membaca CSV dan manifest langsung dari revisi DVC yang dipilih, sehingga pointer/data pada working tree tidak perlu diganti. `--data-rev` diselesaikan menjadi satu commit sebelum semua run; perubahan branch data selama eksperimen tidak mengubah dataset yang sedang dibandingkan. SHA-256 CSV diperiksa terhadap manifest dan dicatat bersama commit data, pointer DVC, commit kode, serta hash skrip training.

Akses Drive perlu dikonfigurasi pada lingkungan yang menjalankan training. **Actions secrets tidak otomatis tersedia di Codespaces.** Tambahkan `GDRIVE_CLIENT_ID`, `GDRIVE_CLIENT_SECRET`, dan `GDRIVE_CREDENTIALS_DATA` sebagai Codespaces secrets dengan akses ke repository, lalu buka ulang Codespace agar environment variable tersedia. Gunakan nilai privat yang sama dengan ingestion, tanpa menyalinnya ke Git atau laporan. Pada komputer lokal yang sudah memiliki `.dvc/config.local` dan kredensial OAuth, konfigurasi tersebut dapat langsung digunakan.

Jika memakai Codespaces secrets, simpan konfigurasi client secara lokal:

```bash
dvc remote modify --local drive gdrive_client_id "$GDRIVE_CLIENT_ID"
dvc remote modify --local drive gdrive_client_secret "$GDRIVE_CLIENT_SECRET"
```

Backend Google Drive membaca JSON kredensial dari `GDRIVE_CREDENTIALS_DATA`. Jangan menampilkan nilai environment variable atau isi `.dvc/config.local` dalam screenshot.

### Menjalankan server dan tiga eksperimen

Terminal pertama, Bash Codespaces:

```bash
mlflow server \
  --backend-store-uri sqlite:///mlflow.db \
  --artifacts-destination ./mlartifacts \
  --host 0.0.0.0 --port 5000 \
  --allowed-hosts "localhost,localhost:*,127.0.0.1,127.0.0.1:*,*.app.github.dev"
```

Buka tab **Ports**, pilih port **5000**, pertahankan visibilitas **Private**, lalu pilih **Open in Browser**. Biarkan server berjalan.

Terminal kedua, dari root repository yang sama:

```bash
python train.py --data-rev origin/data-snapshots --register-best
```

Satu perintah menjalankan tiga training run dengan parameter berbeda, menyimpan model beserta imputer dan scaler melalui `mlflow.sklearn.log_model`, dan mendaftarkan Ridge dengan MAE validasi terendah sebagai alias **candidate**. Registrasi ini belum merupakan deployment. Untuk mengulang versi data yang sama gunakan commit penuh yang tercatat di `output/training/best_model.json`, misalnya:

```bash
python train.py --data-rev dd2545e --alphas 0.1 1 10 --register-best
```

Di Windows, instalasi training bisa diisolasi tanpa aktivasi PowerShell:

```powershell
.\.venv\Scripts\python.exe -m venv .venv-train
.\.venv-train\Scripts\python.exe -m pip install -r requirements-train.txt
# Terminal pertama:
.\.venv-train\Scripts\mlflow.exe server --backend-store-uri sqlite:///mlflow.db --artifacts-destination ./mlartifacts --host 127.0.0.1 --port 5000
# Terminal kedua:
.\.venv-train\Scripts\python.exe train.py --data-rev origin/data-snapshots --register-best
```

### Pembagian waktu dan batas interpretasi

Tanggal validasi default adalah tanggal fitur terbaru; `--validation-start YYYY-MM-DD` dapat memilih batas lain. Training hanya memakai baris yang `snapshot_date + 7 hari <= awal validasi`. Label yang berakhir pada tanggal awal validasi dianggap tersedia setelah snapshot hari tersebut diterima. Baris di antara training dan validasi disisihkan. Imputer median, indikator missing, dan scaler di-fit hanya pada training. `rating_votes` tidak dipakai karena belum tersedia; Bayesian rating digunakan sebagai fitur rating.

Versi 16 Oktober 2026 (`dd2545e`) mempunyai 98 baris dari 50 judul. Pembagian default: 26 training (2 Oktober), 22 disisihkan (3-4 Oktober), dan 50 validasi (9 Oktober). Karena validasi juga digunakan untuk memilih alpha dan belum ada test set independen, metrik adalah hasil eksploratif. Lebih banyak tanggal diperlukan sebelum membuat klaim generalisasi. Nilai kosong chapter berarti metadata observasi tidak mencukupi, bukan otomatis nol aktivitas.

Target awal proyek adalah MAE 10% lebih rendah dari momentum dan NDCG@10 lebih tinggi dari momentum serta popularitas. Hasil pemeriksaan metrik dicatat sebagai `meets_initial_metric_gate`; `deployment_ready` tetap `false` karena evaluasi independen belum tersedia. Model terbaik di antara tiga kandidat tidak harus mengungguli baseline.

### Hasil dan bukti eksperimen

- `output/training/comparison.csv`: perbandingan parameter dan metrik tiga run.
- `output/training/best_model.json`: parameter terpilih, provenance data, pembagian waktu, metrik, serta versi Model Registry.
- `output/training/predictions-alpha-*.csv`: prediksi validasi, target aktual, dan baseline.
- `output/training/metric_comparison.png`: grafik MAE dan RMSE dibandingkan momentum; juga disimpan pada artifact run terbaik.
- `mlflow.db` dan `mlartifacts/`: database dan artefak MLflow lokal; keduanya diabaikan Git.

Pada MLflow UI, buka eksperimen **manhwa-growth-regression**, tampilkan kolom `alpha`, `validation_mae`, `validation_rmse`, dan `validation_ndcg_at_10`, lalu pilih tiga run untuk **Compare**. Bukti yang berguna: daftar tiga run, grafik perbandingan metrik, detail parameter/model, serta halaman **Models** yang memperlihatkan versi kandidat. Screenshot tidak boleh diisi dengan metrik contoh.

Simpan backup database dan artefak sebelum menghapus Codespace. Setelah training selesai dan server dihentikan dengan Ctrl+C:

```bash
tar -czf output/mlflow-backup.tar.gz mlflow.db mlartifacts output/training
```

Unduh arsip tersebut melalui Explorer Codespaces. Hasil MLflow tidak otomatis diunggah ke remote DVC. Pengambilan harian tetap berjalan terpisah pada workflow ingestion; menjalankan training tidak memanggil API MangaDex atau memperbesar cohort.

Referensi: [MLflow Tracking](https://mlflow.org/docs/latest/tracking/), [Model Registry](https://mlflow.org/docs/latest/ml/model-registry/workflow/), [evaluasi temporal scikit-learn](https://scikit-learn.org/stable/modules/cross_validation.html).

## Pengujian

```bash
python -m unittest discover -s tests -v
```

Pemeriksaan training memerlukan `requirements-train.txt`; jika library tersebut belum dipasang, dua pemeriksaan training dilewati dan pengujian ingestion tetap berjalan. Dengan environment training:

```bash
python -m unittest tests.test_training -v
```

Pengujian memastikan kegagalan jaringan tidak memakai fixture, filter katalog hanya memilih bahasa asli Korea, dan duplikasi chapter lintas bahasa mempertahankan waktu ketersediaan paling awal. Uji pengambilan berulang juga memeriksa raw lama tidak berubah meskipun waktu pengambilan sama, snapshot tanggal sebelumnya tetap tersedia, serta satu judul tidak berulang pada tanggal yang sama di interim.

Untuk memverifikasi prapemrosesan sampel saja:

```bash
python -m unittest tests.test_pipeline.PipelineTest.test_committed_live_sample_can_be_preprocessed_repeatedly -v
```

Pengujian memproses ketiga file sampel dua kali di direktori sementara tanpa jaringan. Raw tidak berubah, snapshot harian tidak berlipat, dan checksum processed tetap sama. Nilai `rating_votes` yang tidak disediakan API tetap kosong. File fitur belum memiliki baris karena sampel hanya mencakup satu tanggal.

Proyek ini menggunakan API publik MangaDex, tidak menampilkan isi chapter, dan tidak dimonetisasi. MangaDex tetap harus dicantumkan sebagai sumber data.
