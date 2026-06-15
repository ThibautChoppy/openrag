from pathlib import Path

from config import load_config
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from utils.dependencies import get_vectordb
from utils.logger import get_logger

from .utils import current_user_or_admin_partitions_list

logger = get_logger()

config = load_config()
DATA_DIR = Path(config.paths.data_dir).resolve()

router = APIRouter()


@router.get("/static/{extract_id}", name="download_source")
async def download_source(
    request: Request,
    extract_id: str,
    vectordb=Depends(get_vectordb),
    user_partitions=Depends(current_user_or_admin_partitions_list),
):
    """Download the source document a chunk came from.

    Authorization mirrors ``/extract/{extract_id}``: the caller must have
    access to the partition the chunk belongs to. This replaces the previous
    open ``/static`` mount, which served every tenant's files to any
    authenticated user with no partition check.
    """
    log = logger.bind(extract_id=extract_id)

    chunk = await vectordb.get_chunk_by_id.remote(extract_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found.")

    chunk_partition = chunk.metadata.get("partition")
    if chunk_partition not in user_partitions and user_partitions != ["all"]:
        log.warning("User does not have access to this file.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not have access to this file.",
        )

    source = chunk.metadata.get("source")
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found.")

    # Resolve and confine the path to DATA_DIR to defeat any traversal.
    file_path = Path(source).resolve()
    if not file_path.is_relative_to(DATA_DIR) or not file_path.is_file():
        log.warning("Resolved source path is outside DATA_DIR or missing.")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found.")

    return FileResponse(file_path, filename=file_path.name)
