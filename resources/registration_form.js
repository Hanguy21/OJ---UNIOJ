/* Registration multi-step UI (no Django template syntax — safe for static delivery). */
(function ($) {
    $(function () {
        var $form = $('#edit-form');
        if (!$form.length) {
            return;
        }
        var $steps = $form.find('.register-step');
        var $fill = $('.register-card__progress-fill');
        var $caption = $('#register-progress-caption');
        var $progress = $('#register-progress');
        var captions = [$('#register-cap-1').text(), $('#register-cap-2').text()];

        function setStep(n) {
            n = n === 2 ? 2 : 1;
            $steps.removeClass('register-step--active');
            $steps.filter('[data-register-step="' + n + '"]').addClass('register-step--active');
            $steps.each(function () {
                var on = parseInt($(this).attr('data-register-step'), 10) === n;
                $(this).attr('aria-hidden', on ? 'false' : 'true');
            });
            $fill.css('width', n === 1 ? '50%' : '100%');
            if ($caption.length) {
                $caption.text(captions[n - 1] || '');
            }
            if ($progress.length) {
                $progress.attr('aria-valuenow', n);
            }
        }

        var initial = parseInt($form.attr('data-initial-step'), 10);
        if (initial !== 2) {
            initial = 1;
        }
        setStep(initial);

        $('.register-card__pass-req-toggle').on('click', function () {
            $('.register-card__pass-req').slideToggle('fast');
        });

        try {
            var tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
            if (typeof tz === 'string' && $('#id_timezone option[value="' + tz + '"]').length) {
                $('#id_timezone').val(tz).trigger('change');
            }
        } catch (e) {}

        function wirePwToggle(btnSel, inputSel, ariaShow, ariaHide) {
            var $pw = $(inputSel);
            var $toggle = $(btnSel);
            if (!$toggle.length || !$pw.length) {
                return;
            }
            $toggle.on('click', function () {
                var show = $pw.attr('type') === 'password';
                $pw.attr('type', show ? 'text' : 'password');
                $toggle.attr('aria-pressed', show ? 'true' : 'false');
                var isText = $pw.attr('type') === 'text';
                $toggle.attr('aria-label', isText ? ariaHide : ariaShow);
                $toggle.find('.fa').toggleClass('fa-eye fa-eye-slash');
            });
        }

        var showTxt = $('#reg-aria-show').text();
        var hideTxt = $('#reg-aria-hide').text();
        wirePwToggle('#reg-password1-toggle', '#id_password1', showTxt, hideTxt);
        wirePwToggle('#reg-password2-toggle', '#id_password2', showTxt, hideTxt);

        var $p1 = $('#id_password1');
        var $bar = $('.register-card__pw-strength-bar');
        function scorePassword(s) {
            if (!s) {
                return 0;
            }
            var score = 0;
            if (s.length >= 8) {
                score++;
            }
            if (s.length >= 12) {
                score++;
            }
            if (/[a-z]/.test(s) && /[A-Z]/.test(s)) {
                score++;
            }
            if (/\d/.test(s)) {
                score++;
            }
            if (/[^A-Za-z0-9]/.test(s)) {
                score++;
            }
            return Math.min(score, 4);
        }
        function paintStrength(score) {
            var pct = score * 25;
            var bg = '#d1d5db';
            if (score <= 1) {
                bg = '#ef4444';
            } else if (score === 2) {
                bg = '#f59e0b';
            } else if (score === 3) {
                bg = '#eab308';
            } else if (score >= 4) {
                bg = '#22c55e';
            }
            $bar.css({ width: pct + '%', background: bg });
        }
        $p1.on('input', function () {
            paintStrength(scorePassword($p1.val() || ''));
        });
        paintStrength(scorePassword($p1.val() || ''));

        $('#register-step-next').on('click', function () {
            var $step = $form.find('.register-step[data-register-step="1"]');
            var ok = true;
            $step.find('input, select, textarea').each(function () {
                var el = this;
                if (typeof el.checkValidity === 'function' && !el.checkValidity()) {
                    el.reportValidity();
                    ok = false;
                    return false;
                }
            });
            if (ok) {
                setStep(2);
            }
        });

        $('#register-step-back').on('click', function () {
            setStep(1);
        });

        $form.on('submit', function () {
            $('#register-submit').prop('disabled', true).addClass('is-loading');
        });
    });
})(window.jQuery);
