# Lifecycle request pada TUI

`idle → waiting → streaming → completed|stopped|error`; render menggunakan state
yang sudah ada dan tidak memanggil transport. Memulai request tidak menunggu
koneksi selesai. Ticker status berjalan sendiri saat upstream belum mengirim byte;
jangan tampilkan kalimat jawaban buatan seolah-olah delta model.

Setiap job memiliki generation ID. Saat stop/retry/new request, invalidasi ID lama
sebelum membatalkan task. Channel membawa ID agar event yang sudah terantre pun
diabaikan ketika basi. Closure stream tidak boleh mematikan worker lain. Gunakan
backpressure atau kebijakan buffer eksplisit, bukan list tanpa batas.

Controller contoh menyediakan `start`, `cancel`, `poll`, `close`; UI memanggil poll
nonblocking. Batas `timeout` pada contoh adalah deadline total. Transport produksi
masih harus menerapkan deadline koneksi, idle, HTTP, dan penutupan socketnya sendiri.
Async iterator yang memblokir thread tidak dapat diselamatkan oleh asyncio timeout;
pindahkan pekerjaan tersebut ke runtime background sesuai proyek.

Uji worker yang ditahan Event/Future, UI tick yang tetap diproses, stop berulang,
request baru sebelum hasil lama, queue penuh, exception provider yang teredaksi,
timeout, serta penutupan saat task aktif. Virtual clock/barrier lebih baik daripada
sleep panjang dan assert berdasarkan kebetulan timing.

Referensi: https://docs.python.org/3/library/asyncio-task.html#task-cancellation
