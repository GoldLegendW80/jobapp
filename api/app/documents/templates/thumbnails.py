"""Make a template's thumbnails through the real renderer:

    python -m app.documents.templates.thumbnails <slug> [--host renderer] [--port 7071]

For each kind, the invented sample of `_base/sample.json` ("Alex Example" at "Example Corp") is
fitted to one page with the template's steps (fit.fit, render_client.render), and page 1 is
written as `thumbnail.png` or `thumbnail-letter.png`, THUMBNAIL_WIDTH pixels wide; then the
manifest's `thumbnail_of` is set to the hash of the folder, which the template tests compare.
Run it wherever the renderer answers (the worker's container in the stack).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import date

from app.documents import content as c
from app.documents import fit, profile_view, render_client, templates
from app.documents.render_client import Rendered

Render = Callable[[str], Awaitable[Rendered]]


def sample() -> dict:
    return json.loads((templates.BASE / "sample.json").read_text(encoding="utf-8"))


def sample_view() -> profile_view.ProfileView:
    return profile_view.build(sample()["profile"], revision=1)


async def make(slug: str, render: Render | None = None) -> dict[str, fit.Fitted]:
    """Writes the two thumbnails of the folder `slug` and its `thumbnail_of`; returns the fits."""
    folder = templates.ROOT / slug
    template = templates.load_folder(folder, thumbnails=False)
    data = sample()
    view = sample_view()
    rendered_on = date.fromisoformat(data["rendered_on"])
    photo = None
    fitted: dict[str, fit.Fitted] = {}
    for kind, name in zip(templates.KINDS, templates.THUMBNAILS, strict=True):
        content = c.MODELS[kind].model_validate(data[kind])

        def build(step: templates.Step, kind: str = kind, content: c.Content = content) -> str:
            return templates.build_html(
                template, kind, content, view, "en", step, rendered_on, photo=photo
            )

        result = await fit.fit(template.steps(kind), build, render=render)
        png = render_client.page1_png(result.pdf, templates.THUMBNAIL_WIDTH)
        (folder / name).write_bytes(png)
        fitted[kind] = result
    manifest_path = folder / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["thumbnail_of"] = templates.thumbnail_hash(folder)
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return fitted


def main() -> None:
    parser = argparse.ArgumentParser(description="Make the thumbnails of one template.")
    parser.add_argument("slug")
    parser.add_argument("--host", default=render_client.HOST)
    parser.add_argument("--port", type=int, default=render_client.PORT)
    arguments = parser.parse_args()

    async def render(html: str) -> Rendered:
        return await render_client.render(html, host=arguments.host, port=arguments.port)

    for kind, result in asyncio.run(make(arguments.slug, render)).items():
        print(f"{arguments.slug} {kind}: step {result.step}, pages {result.pages}")


if __name__ == "__main__":
    main()
