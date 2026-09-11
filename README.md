# Projek-MLOps

# Web Comic Recommendation System (MLOps)

Repositori ini berisi fondasi teknis dan *pipeline* Machine Learning Operations (MLOps) untuk pengembangan Sistem Rekomendasi Web Komik Berbasis *Clickstream* dan API.

## 🎯 Tujuan Proyek
Tujuan dari proyek ini adalah membangun sistem rekomendasi yang bersifat dinamis (menggunakan *Continual Learning*) untuk mengatasi masalah *churn rate* pengguna dan *item cold-start* pada platform baca komik. Sistem ini secara periodik akan menarik data metadata komik terbaru via REST API publik (MangaDex API) dan memproses interaksi bacaan pengguna untuk melatih ulang bobot model rekomendasi secara otomatis.

## 📂 Struktur Direktori
Struktur repositori ini mengadopsi standar konvensi *Cookiecutter Data Science*:

├── configs/   # File konfigurasi (YAML, JSON) untuk pipeline dan hyperparameter
├── data/      # Folder penyimpanan raw data dan processed data (diabaikan oleh git)
├── docs/      # Dokumentasi proyek, desain arsitektur, dan referensi
├── models/    # Artefak model yang telah dilatih (pickles, weights)
├── notebooks/ # Jupyter notebooks untuk eksplorasi data (EDA) dan prototipe awal
├── src/       # Source code utama (data ingestion, preprocessing, training, serving)
├── tests/     # Unit tests untuk menguji keandalan modul Python
├── .gitignore
├── requirements.txt
└── README.md

## 🚀 Instruksi Penggunaan (Codespaces)
Proyek ini dirancang agar *reproducible* tanpa perlu instalasi manual di perangkat lokal. Anda dapat langsung menjalankan *environment* ini menggunakan **GitHub Codespaces**.

1. Navigasi ke halaman utama repositori ini di GitHub.
2. Klik tombol hijau **<> Code**.
3. Pilih tab **Codespaces** dan klik **Create codespace on main**.
4. Sistem akan secara otomatis menyiapkan *container* berbasis Python 3.10 dan menginstal seluruh dependensi yang ada pada `requirements.txt` berkat konfigurasi `.devcontainer`.
5. Anda siap melakukan eksperimen melalui Jupyter Notebook di folder `notebooks/` atau menulis kode produksi di folder `src/`.