# Harmonika Engine Registry

Auto-discovery operator-managed untuk backend chat.harmonika.id.
`discovery.py` menyediakan `Discovery`, snapshot atomik, audit SQLite,
verifikasi source, async execute terisolasi proses dan hot reload.
`schema.py` memvalidasi subset JSON Schema tanpa mengambil skema dari jaringan.

[Kontrak, instalasi, keamanan dan operasi](../../docs/ENGINE-DISCOVERY.md).
[Report pengujian dan aktivasi](../../docs/ENGINE-DISCOVERY-REPORT.json).

`skills/text-metrics` adalah implementasi awal yang dipasang; daftar skill baru
tidak ditulis ke adapter registry. Modul operator baru dipindai otomatis.
Semua handler lama dan context/scope engine asal dipertahankan.
