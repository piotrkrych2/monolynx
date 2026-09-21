---
name: patterns-nh3-sanitization
description: nh3.clean() confirmed API params for sanitizing markdown->HTML before it reaches an unauthenticated public reader (MON-201)
metadata:
  type: reference
---

Monolynx added `nh3` (Rust/ammonia HTML sanitizer bindings) as a dependency for the public
`/blog/{slug}` route (`src/monolynx/services/wiki.py::render_markdown_public`), which sits
downstream of `markdown.markdown()`. `render_markdown_html()` (dashboard, session+RBAC
protected) stays unsanitized on purpose — a regression test pins that.

Confirmed `nh3.clean()` signature (verified via docs, not by running Python locally):

```python
nh3.clean(html, tags=None, clean_content_tags=None, attributes=None,
          attribute_filter=None, strip_comments=True, link_rel='noopener noreferrer',
          generic_attribute_prefixes=None, tag_attribute_values=None,
          set_tag_attribute_values=None, url_schemes=None, allowed_classes=None,
          filter_style_properties=None, url_relative=None, id_prefix=None)
```

Pattern used: pass explicit `tags` (set[str]), `attributes` (dict[str, set[str]]),
`url_schemes`, and `clean_content_tags={"script", "style"}` rather than relying on
ammonia's built-in defaults — defensive against the library's default allow-list changing
across versions. A tag not present as a key in `attributes` gets ALL its attributes
stripped (this is how `onclick`/`onerror`/`onload` get removed without an explicit
denylist — allow-list the attributes you want, not the ones you don't). `url_schemes`
restricts `href`/`src` to e.g. `{"http", "https", "mailto"}`; a disallowed scheme (like
`javascript:`) causes the attribute to be dropped, not the whole tag.

See [[patterns_partial_unique_index_no_project_scope]] for the sibling MON-201 pattern
(global partial unique index for the same feature).
