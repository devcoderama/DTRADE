# Contributors

Terima kasih kepada semua yang berkontribusi pada `binance-ai-future`.

## Pemilik & Maintainer

- **Ramadhan Wardiyana** — [@devcoderama](https://github.com/devcoderama)
  Pemilik proyek, pengujian, dan keputusan akhir rilis.

## Kontributor

- **Ember (Muse AI Assistant)** — arsitektur awal, penulisan kode inti
  (`core/`, `cli/`, `web/`), dokumentasi `STRUKTUR.MD` dan `README.MD`.

<!-- Tambahkan nama Anda di daftar ini pada pull request pertama Anda. -->

## Cara Berkontribusi

1. Fork repository ini, buat branch fitur: `git checkout -b fitur/nama-fitur`.
2. Ikuti peta modul di [STRUKTUR.MD](STRUKTUR.MD) — logika bisnis hanya di
   `core/`, antarmuka (CLI/Web) tetap tipis.
3. Pastikan kode lolos pemeriksaan dasar:
   `python -m py_compile $(find . -name "*.py" -not -path "./.venv/*")`
4. Jangan pernah meng-commit `.env` atau kredensial apa pun. Gunakan
   `.env.example` sebagai satu-satunya referensi nama variabel.
5. Buka Pull Request dengan deskripsi singkat: apa yang berubah, bagaimana
   cara mengujinya, dan tangkapan layar bila menyentuh Web UI.

## Kode Etik Singkat

Bersikap sopan, fokus pada kode bukan orangnya, dan ingat: proyek ini
berhubungan dengan uang sungguhan — perubahan pada `core/risk.py` dan
`core/executor.py` wajib dijelaskan ekstra hati-hati di Pull Request.
