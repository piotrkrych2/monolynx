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

    var VIDEO_EMBEDS = {
        youtube: {
            id: /^[A-Za-z0-9_-]{6,20}$/,
            url: "https://www.youtube-nocookie.com/embed/"
        },
        vimeo: {
            id: /^\d+$/,
            url: "https://player.vimeo.com/video/"
        }
    };

    // Adres iframe powstaje tylko z providera i id, nigdy z href linku.
    function videoEmbedUrl(provider, id) {
        if (!Object.prototype.hasOwnProperty.call(VIDEO_EMBEDS, provider)) {
            return null;
        }
        var spec = VIDEO_EMBEDS[provider];
        if (!spec.id.test(id)) {
            return null;
        }
        return spec.url + id + "?autoplay=1";
    }

    document.addEventListener("click", function (event) {
        var target = event.target;
        var play = target && target.closest ? target.closest(".blog-video-play") : null;
        if (play) {
            var video = play.closest(".blog-video");
            if (!video) {
                return;
            }
            var src = videoEmbedUrl(
                video.getAttribute("data-provider") || "",
                video.getAttribute("data-video-id") || ""
            );
            if (!src) {
                return;
            }
            event.preventDefault();
            var frame = document.createElement("iframe");
            frame.setAttribute("src", src);
            frame.setAttribute("title", play.textContent || "Wideo");
            frame.setAttribute("allow", "autoplay; encrypted-media; picture-in-picture; fullscreen");
            frame.setAttribute("allowfullscreen", "");
            frame.setAttribute("loading", "lazy");
            frame.setAttribute("referrerpolicy", "strict-origin-when-cross-origin");
            video.replaceChild(frame, play);
            return;
        }
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
