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

    // Spis treści (MON-260): bez JS linki działają, a <details> jest zwinięty.
    var WIDE_QUERY = "(min-width: 1024px)";

    function syncTocLayout(wrap) {
        if (!window.matchMedia) {
            return;
        }
        var query = window.matchMedia(WIDE_QUERY);
        function apply() {
            wrap.open = query.matches;
            wrap.classList.toggle("is-wide", query.matches);
        }
        apply();
        if (query.addEventListener) {
            query.addEventListener("change", apply);
        }
    }

    function highlightToc(toc) {
        if (!("IntersectionObserver" in window)) {
            return;
        }
        var linkById = {};
        var headings = [];
        toc.querySelectorAll('a[href^="#"]').forEach(function (link) {
            var id;
            try {
                id = decodeURIComponent(link.getAttribute("href").slice(1));
            } catch (e) {
                return;
            }
            var heading = id ? document.getElementById(id) : null;
            if (heading) {
                linkById[id] = link;
                headings.push(heading);
            }
        });
        if (!headings.length) {
            return;
        }

        var visible = {};
        var current = null;

        function activate(id) {
            if (id === current) {
                return;
            }
            if (current && linkById[current]) {
                linkById[current].classList.remove("is-active");
                linkById[current].removeAttribute("aria-current");
            }
            current = id;
            if (id && linkById[id]) {
                linkById[id].classList.add("is-active");
                linkById[id].setAttribute("aria-current", "true");
            }
        }

        var observer = new IntersectionObserver(
            function (entries) {
                entries.forEach(function (entry) {
                    visible[entry.target.id] = entry.isIntersecting;
                });
                for (var i = 0; i < headings.length; i++) {
                    if (visible[headings[i].id]) {
                        activate(headings[i].id);
                        return;
                    }
                }
            },
            // Górny margines pomija navbar; dolny zostawia aktywną ostatnią sekcję w górnej części ekranu.
            { rootMargin: "-80px 0px -70% 0px" }
        );
        headings.forEach(function (heading) { observer.observe(heading); });
    }

    function initToc() {
        var toc = document.querySelector("nav.blog-toc");
        if (!toc) {
            return;
        }
        var wrap = toc.closest("details");
        if (wrap) {
            syncTocLayout(wrap);
        }
        highlightToc(toc);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initToc);
    } else {
        initToc();
    }
})();
