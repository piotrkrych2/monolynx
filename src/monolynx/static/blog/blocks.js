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

    var TYPING_MAX_MS = 1800;
    var TYPING_TICK_MS = 30;

    function prefersReducedMotion() {
        return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    }

    function initDiagrams() {
        var diagrams = document.querySelectorAll("[data-blog-diagram]");
        if (!diagrams.length || !window.mermaid) {
            return;
        }
        var dark = document.documentElement.classList.contains("dark");
        try {
            window.mermaid.initialize({
                startOnLoad: false,
                securityLevel: "strict",
                theme: dark ? "dark" : "default"
            });
        } catch (e) {
            return;
        }
        Array.prototype.forEach.call(diagrams, function (figure, index) {
            var canvas = figure.querySelector(".blog-diagram-canvas");
            var source = figure.querySelector(".blog-diagram-source code");
            if (!canvas || !source) {
                return;
            }
            try {
                Promise.resolve(
                    window.mermaid.render("blog-diagram-svg-" + index, source.textContent)
                ).then(function (result) {
                    // SVG zwraca biblioteka przy securityLevel strict, nie treść autora.
                    canvas.innerHTML = result.svg;
                    canvas.hidden = false;
                    figure.classList.add("blog-diagram-rendered");
                }, function () {
                    var leftover = document.getElementById("dblog-diagram-svg-" + index);
                    if (leftover && leftover.parentNode) {
                        leftover.parentNode.removeChild(leftover);
                    }
                });
            } catch (e) {
                return;
            }
        });
    }

    function typeTerminal(figure) {
        var code = figure.querySelector(".blog-terminal-body code");
        if (!code) {
            return;
        }
        var full = code.textContent;
        if (!full) {
            return;
        }
        var body = code.parentNode;
        body.style.minHeight = body.offsetHeight + "px";
        var step = Math.max(1, Math.ceil(full.length / (TYPING_MAX_MS / TYPING_TICK_MS)));
        var position = 0;
        code.textContent = "";
        figure.classList.add("blog-terminal-typing");
        function tick() {
            position = Math.min(full.length, position + step);
            code.textContent = full.slice(0, position);
            if (position < full.length) {
                window.setTimeout(tick, TYPING_TICK_MS);
                return;
            }
            figure.classList.remove("blog-terminal-typing");
            body.style.minHeight = "";
        }
        tick();
    }

    function initTerminals() {
        if (prefersReducedMotion()) {
            return;
        }
        var terminals = document.querySelectorAll("[data-blog-terminal]");
        Array.prototype.forEach.call(terminals, function (figure) {
            if (!("IntersectionObserver" in window)) {
                typeTerminal(figure);
                return;
            }
            var observer = new IntersectionObserver(function (entries) {
                if (entries.some(function (entry) { return entry.isIntersecting; })) {
                    observer.disconnect();
                    typeTerminal(figure);
                }
            });
            observer.observe(figure);
        });
    }

    function initGraphics() {
        var graphics = window.BlogGraphics;
        var nodes = document.querySelectorAll("[data-blog-graphic]");
        if (!graphics) {
            return;
        }
        Array.prototype.forEach.call(nodes, function (el) {
            var name = el.getAttribute("data-blog-graphic") || "";
            if (!Object.prototype.hasOwnProperty.call(graphics, name)) {
                return;
            }
            try {
                graphics[name].mount(el);
                el.classList.add("blog-graphic-mounted");
            } catch (e) {
                // Zostaje sam opis z HTML.
            }
        });
    }

    function initBlocks() {
        [initDiagrams, initTerminals, initGraphics].forEach(function (init) {
            try {
                init();
            } catch (e) {
                // Jeden blok nie psuje pozostałych.
            }
        });
    }

    // Skrypty defer wykonują się przed DOMContentLoaded, więc Mermaid i komponenty są już zarejestrowane.
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initBlocks);
    } else {
        initBlocks();
    }
})();
