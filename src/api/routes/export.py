"""Export (§6): every file carries its attribution and its rights."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from src.answer.render import to_jsonable, to_markdown
from src.api.deps import store

router = APIRouter(tags=["export"])


@router.get("/export/{answer_id}")
def export_answer(
    answer_id: str,
    format: str = Query("markdown", pattern="^(markdown|json)$"),
) -> Response:
    answer = store().answer(answer_id)
    pack = store().pack(answer_id)
    sources = store().sources(answer_id)
    if answer is None or pack is None:
        raise HTTPException(status_code=404, detail="answer not found")

    if format == "json":
        body = json.dumps(to_jsonable(answer, pack, sources), indent=2, ensure_ascii=False)
        media = "application/json"
        ext = "json"
    else:
        body = to_markdown(answer, pack, sources, view="export")
        media = "text/markdown; charset=utf-8"
        ext = "md"

    # Export is the boundary where redistribution is checked: a source that may
    # be displayed but not redistributed renders as a citation inside the file.
    if not answer.attribution:
        raise HTTPException(
            status_code=500,
            detail="refusing to export an answer with no attribution",
        )

    return Response(
        content=body,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="answer-{answer_id}.{ext}"'},
    )
