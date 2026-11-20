from typing import Annotated

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.config import MAX_INPUT_BYTES
from app.modules.intake.decode import decode_table
from app.modules.revision.service import SAMPLE_FILE, load_sample_supplement, run_revision

router = APIRouter(prefix='/api/review')
RunId = Annotated[str, Path(pattern=r'^[0-9a-f]{64}$')]


class RevisionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')

    filename: Annotated[str, Field(pattern=r'^[^/\\]{1,60}\.csv$')] = SAMPLE_FILE
    content: Annotated[str | None, Field(max_length=MAX_INPUT_BYTES)] = None


@router.post('/runs/{run_id}/revisions')
def revise(request: Request, run_id: RunId, form: RevisionInput):
    snapshot, reused = run_revision(request.app.state.settings, request.app.state.store, run_id, form)
    return {'reused': reused, **snapshot}


@router.get('/resurvey-sample')
def resurvey_sample(request: Request, dataset: Annotated[str, Query(pattern='^(field|reference)$')] = 'field'):
    raw = load_sample_supplement(request.app.state.settings, dataset)
    text, _ = decode_table(raw, SAMPLE_FILE)
    return {'filename': SAMPLE_FILE, 'content': text}
