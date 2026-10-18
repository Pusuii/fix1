/* Learnova - interaksi ringan (tanpa dependensi) */
(function () {
  var body = document.body;

  // Menu samping (mobile)
  document.addEventListener('click', function (e) {
    if (e.target.closest('[data-nav-toggle]')) { body.classList.toggle('nav-open'); }
    else if (e.target.closest('[data-nav-close]')) { body.classList.remove('nav-open'); }
    var dismiss = e.target.closest('[data-dismiss]');
    if (dismiss) { var a = dismiss.closest('.alert'); if (a) a.remove(); }
  });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') body.classList.remove('nav-open'); });

  // Konfirmasi sebelum aksi berisiko
  document.addEventListener('submit', function (e) {
    var msg = e.target.getAttribute('data-confirm');
    if (msg && !window.confirm(msg)) { e.preventDefault(); }
  });

  // Cegah klik ganda pada tombol submit
  document.addEventListener('submit', function (e) {
    if (e.defaultPrevented) return;
    var btn = e.target.querySelector('button[type=submit]:not([data-keep])');
    if (btn) { setTimeout(function () { btn.disabled = true; }, 0); }
  });

  // Tampilkan / sembunyikan password
  document.querySelectorAll('[data-toggle-password]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var input = document.getElementById(btn.getAttribute('data-toggle-password'));
      if (!input) return;
      input.type = input.type === 'password' ? 'text' : 'password';
      btn.setAttribute('aria-pressed', input.type === 'text');
    });
  });

  // Isi otomatis akun demo di halaman login
  document.querySelectorAll('[data-fill-email]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var email = document.getElementById('f-email'), pw = document.getElementById('f-password');
      if (email) email.value = btn.getAttribute('data-fill-email');
      if (pw) pw.value = btn.getAttribute('data-fill-password');
      if (pw) pw.focus();
    });
  });

  // Penghitung waktu kuis
  var timer = document.querySelector('[data-deadline-ts]');
  if (timer) {
    var end = parseInt(timer.getAttribute('data-deadline-ts'), 10) * 1000;
    var form = document.getElementById('quiz-form');
    var out = timer.querySelector('[data-timer-text]');
    var skew = parseInt(timer.getAttribute('data-now-ts'), 10) * 1000 - Date.now();
    var tick = function () {
      var left = Math.max(0, Math.floor((end - (Date.now() + skew)) / 1000));
      var m = Math.floor(left / 60), s = left % 60;
      out.textContent = (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s;
      if (left <= 60) timer.classList.add('low');
      if (left <= 0) { clearInterval(iv); if (form) { form.removeAttribute('data-confirm'); form.submit(); } }
    };
    var iv = setInterval(tick, 1000); tick();
  }

  // Tampilkan/ubah bidang form berdasarkan pilihan (data-toggle-target)
  document.querySelectorAll('[data-switch]').forEach(function (sel) {
    var apply = function () {
      document.querySelectorAll('[data-show-when]').forEach(function (el) {
        var rule = el.getAttribute('data-show-when').split(':');
        if (rule[0] !== sel.getAttribute('data-switch')) return;
        el.hidden = rule[1].split('|').indexOf(sel.value) === -1;
      });
    };
    sel.addEventListener('change', apply); apply();
  });
})();
