(function () {
    var RESET_MS = 2000;

    function fallbackCopy(text) {
        var area = document.createElement("textarea");
        area.value = text;
        area.setAttribute("readonly", "");
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.appendChild(area);
        area.select();
        var ok = false;
        try {
            ok = document.execCommand("copy");
        } catch (e) {
            ok = false;
        }
        document.body.removeChild(area);
        return ok ? Promise.resolve() : Promise.reject(new Error("copy failed"));
    }

    function copyText(text) {
        if (navigator.clipboard && window.isSecureContext) {
            return navigator.clipboard.writeText(text).catch(function () {
                return fallbackCopy(text);
            });
        }
        return fallbackCopy(text);
    }

    function flash(button, label) {
        button.textContent = label;
        window.clearTimeout(button._blogCopyTimer);
        button._blogCopyTimer = window.setTimeout(function () {
            button.textContent = "Kopiuj";
        }, RESET_MS);
    }

    document.addEventListener("click", function (event) {
        var target = event.target;
        var button = target && target.closest ? target.closest(".blog-code-copy") : null;
        if (!button) {
            return;
        }
        var wrapper = button.closest(".blog-code");
        var code = wrapper ? wrapper.querySelector("pre code") : null;
        if (!code) {
            return;
        }
        copyText(code.textContent).then(
            function () { flash(button, "Skopiowano"); },
            function () { flash(button, "Nie udało się"); }
        );
    });
})();
