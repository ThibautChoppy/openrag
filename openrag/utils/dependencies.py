from functools import wraps

import ray
from components.indexer.indexer import Indexer, TaskStateManager
from components.indexer.loaders.audio import WhisperActor, WhisperPool
from components.indexer.loaders.pdf_loaders.docling2 import DoclingPool
from components.indexer.loaders.pdf_loaders.marker import MarkerPool
from components.indexer.loaders.serializer import DocSerializer
from components.indexer.vectordb import ConnectorFactory
from config import load_config
from services.inference.distributed_semaphore import DistributedSemaphoreActor
from utils.logger import get_logger

# load config
config = load_config()
logger = get_logger()

actor_creation_map: dict[str, callable] = {}


def _track_actor(func):
    @wraps(func)
    def wrapper(name, cls, namespace="openrag", remote_args=(), **options):
        actor_creation_map[name] = lambda: func(name, cls, namespace=namespace, remote_args=remote_args, **options)
        return func(name, cls, namespace=namespace, remote_args=remote_args, **options)

    return wrapper


@_track_actor
def get_or_create_actor(name, cls, namespace="openrag", remote_args=(), **options):
    try:
        return ray.get_actor(name, namespace=namespace)
    except ValueError:
        return cls.options(name=name, namespace=namespace, **options).remote(*remote_args)
    except Exception:
        raise


def get_task_state_manager():
    return get_or_create_actor("TaskStateManager", TaskStateManager, lifetime="detached")


def get_serializer():
    return get_or_create_actor("DocSerializer", DocSerializer, lifetime="detached")


def get_marker_pool():
    pdf_loader = config.loader.file_loaders.pdf
    match pdf_loader:
        case "DoclingLoader2":
            return get_or_create_actor("DoclingPool", DoclingPool, lifetime="detached")
        case "MarkerLoader":
            return get_or_create_actor("MarkerPool", MarkerPool, lifetime="detached")


def get_indexer():
    return get_or_create_actor("Indexer", Indexer, lifetime="detached")


def get_vectordb():
    vectordb_cls = ConnectorFactory().get_vectordb_cls()
    actor = get_or_create_actor("Vectordb", vectordb_cls, lifetime="detached")
    # Open the asyncpg pool + materialise the Milvus collection inside the
    # actor's own asyncio loop. ray.get() blocks here but the work runs
    # remotely, so the pool is bound to the actor's loop (not a one-shot
    # init loop). Both stores guard with internal "_loaded" flags, so
    # re-running this on a hot actor is a no-op.
    ray.get(actor.initialize.remote())

    # Override the restart-endpoint hook (see routers/actors.py) so a
    # restarted Vectordb actor goes through the same initialize step.
    def _recreate_and_init():
        new_actor = get_or_create_actor("Vectordb", vectordb_cls, lifetime="detached")
        ray.get(new_actor.initialize.remote())
        return new_actor

    actor_creation_map["Vectordb"] = _recreate_and_init
    return actor


def init_audio_actor():
    use_whisper_lang_detector = config.loader.transcriber.use_whisper_lang_detector
    file_loaders = config.loader.file_loaders
    loader_values = set(file_loaders.values()) if file_loaders else set()

    if "LocalWhisperLoader" in loader_values:
        return get_or_create_actor("WhisperPool", WhisperPool, lifetime="detached")

    if "OpenAIAudioLoader" in loader_values and use_whisper_lang_detector:
        return get_or_create_actor("WhisperActor", WhisperActor, lifetime="detached")


def init_llm_semaphore():
    return get_or_create_actor(
        "llmSemaphore",
        DistributedSemaphoreActor,
        lifetime="detached",
        remote_args=(config.semaphore.llm_semaphore,),
    )


def init_vlm_semaphore():
    return get_or_create_actor(
        "vlmSemaphore",
        DistributedSemaphoreActor,
        lifetime="detached",
        remote_args=(config.semaphore.vlm_semaphore,),
    )


def init_audio_semaphore():
    return get_or_create_actor(
        "audioSemaphore",
        DistributedSemaphoreActor,
        lifetime="detached",
        remote_args=(config.loader.transcriber.max_concurrent_chunks,),
    )


init_llm_semaphore()
init_vlm_semaphore()
init_audio_semaphore()
init_audio_actor()
get_marker_pool()

task_state_manager = get_task_state_manager()
serializer = get_serializer()
vectordb = get_vectordb()
indexer = get_indexer()
