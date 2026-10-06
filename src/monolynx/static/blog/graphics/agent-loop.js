(function () {
    var SVG_NS = "http://www.w3.org/2000/svg";
    var STEP_MS = 1400;

    var NODES = [
        { label: "Ticket", x: 30, y: 30 },
        { label: "Agent", x: 300, y: 30 },
        { label: "Merge request", x: 300, y: 174 },
        { label: "Wiki", x: 30, y: 174 }
    ];
    var NODE_W = 150;
    var NODE_H = 56;

    // Strzałka i: z węzła i do węzła i + 1 (ostatnia wraca do pierwszego).
    var ARROWS = [
        { x1: 180, y1: 58, x2: 296, y2: 58 },
        { x1: 375, y1: 86, x2: 375, y2: 170 },
        { x1: 300, y1: 202, x2: 184, y2: 202 },
        { x1: 105, y1: 174, x2: 105, y2: 90 }
    ];

    function svgEl(name, attrs) {
        var node = document.createElementNS(SVG_NS, name);
        Object.keys(attrs || {}).forEach(function (key) {
            node.setAttribute(key, attrs[key]);
        });
        return node;
    }

    function build() {
        var svg = svgEl("svg", {
            "class": "agent-loop-svg",
            viewBox: "0 0 480 260",
            "aria-hidden": "true",
            focusable: "false"
        });
        var defs = svgEl("defs");
        var marker = svgEl("marker", {
            id: "agent-loop-arrowhead",
            viewBox: "0 0 10 10",
            refX: "8",
            refY: "5",
            markerWidth: "7",
            markerHeight: "7",
            orient: "auto-start-reverse"
        });
        marker.appendChild(svgEl("path", { d: "M0 0 L10 5 L0 10 z", "class": "agent-loop-arrowhead" }));
        defs.appendChild(marker);
        svg.appendChild(defs);

        var arrows = ARROWS.map(function (a) {
            var line = svgEl("line", {
                x1: a.x1, y1: a.y1, x2: a.x2, y2: a.y2,
                "class": "agent-loop-arrow",
                "marker-end": "url(#agent-loop-arrowhead)"
            });
            svg.appendChild(line);
            return line;
        });

        var nodes = NODES.map(function (n, index) {
            var group = svgEl("g", { "class": "agent-loop-node" });
            group.appendChild(svgEl("rect", {
                x: n.x, y: n.y, width: NODE_W, height: NODE_H, rx: 10, ry: 10
            }));
            var step = svgEl("text", {
                x: n.x + 14, y: n.y + 20, "class": "agent-loop-step"
            });
            step.textContent = String(index + 1);
            group.appendChild(step);
            var label = svgEl("text", {
                x: n.x + NODE_W / 2, y: n.y + 38, "text-anchor": "middle", "class": "agent-loop-label"
            });
            label.textContent = n.label;
            group.appendChild(label);
            svg.appendChild(group);
            return group;
        });

        var center = svgEl("text", {
            x: 240, y: 134, "text-anchor": "middle", "class": "agent-loop-center"
        });
        center.textContent = "Pętla pracy agenta";
        svg.appendChild(center);

        return { svg: svg, nodes: nodes, arrows: arrows };
    }

    function mount(el) {
        var parts = build();
        var stage = document.createElement("div");
        stage.className = "agent-loop";
        stage.appendChild(parts.svg);
        el.appendChild(stage);

        if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
            return;
        }

        var current = 0;
        function highlight() {
            parts.nodes.forEach(function (node, index) {
                node.classList.toggle("is-active", index === current);
            });
            parts.arrows.forEach(function (arrow, index) {
                arrow.classList.toggle("is-active", index === current);
            });
            current = (current + 1) % parts.nodes.length;
        }
        highlight();
        var timer = window.setInterval(function () {
            if (!el.isConnected) {
                window.clearInterval(timer);
                return;
            }
            if (!document.hidden) {
                highlight();
            }
        }, STEP_MS);
    }

    window.BlogGraphics = window.BlogGraphics || {};
    window.BlogGraphics["agent-loop"] = { mount: mount };
})();
