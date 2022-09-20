#!/bin/env python3
#
# Copyright (c) 2022 Kairo de Araujo
#
#
import json
import logging
from enum import Enum
from typing import Any, Dict, Optional

import redis
from celery import Celery, schedules, signals

from repo_worker import worker_settings
from repo_worker.repository import MetadataRepository

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)


class status(Enum):
    RECEIVED = "RECEIVED"
    PRE_RUN = "PRE_RUN"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    UNKNOWN = "UNKNOWN"
    FAILURE = "FAILURE"


redis_backend = redis.StrictRedis.from_url(worker_settings.REDIS_SERVER)

# TODO: Issue https://github.com/KAPRIEN/kaprien/issues/6
# BROKER_USE_SSL = {
#     "keyfile": "data/certs/engine_mq.pem",
#     "certfile": "data/certs/engine_mq.crt",
#     "ca_certs": "data/certs/ca_mq.crt",
#     "cert_reqs": ssl.CERT_REQUIRED,
# }

app = Celery(
    f"kaprien_repo_worker_{worker_settings.WORKER_ID}",
    broker=worker_settings.BROKER_SERVER,
    backend=worker_settings.REDIS_SERVER,
    result_persistent=True,
    task_acks_late=True,
    task_track_started=True,
    broker_heartbeat=0,
    # broker_use_ssl=BROKER_USE_SSL
    # (https://github.com/KAPRIEN/kaprien/issues/6)
)


@app.task(serializer="json")
def kaprien_repo_worker(action: str, payload: Optional[Dict[str, Any]] = None):
    """
    Kaprien Metadata Repository Worker
    """
    repository.refresh_settings(worker_settings)
    repository_action = getattr(repository, action)

    if payload is None:
        result = repository_action()
    else:
        result = repository_action(payload)

    return result


def _publish_signals(
    status: status, task_id: str, result: Optional[str] = None
):
    """
    Publishes Signals to the Result Backend.

    Args:
        status: Task status
        task_id: Task identification
        result: Result about the Task
    """
    redis_backend.set(
        f"celery-task-meta-{task_id}",
        json.dumps(
            {"status": status.value, "task_id": task_id, "result": result}
        ),
    )


@signals.task_prerun.connect(sender=kaprien_repo_worker)
def task_pre_run_notifier(**kwargs):
    """Publishes Signal when task is in PRE_RUN state"""
    logging.debug((f"{status.PRE_RUN.value}: {kwargs.get('task_id')}"))
    _publish_signals(status.PRE_RUN, kwargs.get("task_id"))


@signals.task_unknown.connect(sender=kaprien_repo_worker)
def task_unknown_notifier(**kwargs):
    """Publishes Signal when task is in UNKNOWN state"""
    logging.debug((f"{status.UNKNOWN.value}: {kwargs.get('task_id')}"))
    _publish_signals(status.UNKNOWN, kwargs.get("task_id"))


@signals.task_received.connect(sender=kaprien_repo_worker)
def task_received_notifier(**kwargs):
    """Publishes Signal when task is in RECEIVED state"""
    logging.debug((f"{status.RECEIVED}: {kwargs.get('task_id')}"))
    _publish_signals(status.RECEIVED, kwargs.get("task_id"))


app.conf.beat_schedule = {
    "bump_online_roles": {
        "task": "app.kaprien_repo_worker",
        "schedule": schedules.crontab(minute="*/10"),
        "kwargs": {
            "action": "bump_online_roles",
        },
        "options": {
            "task_id": "bump_online_roles",
            "queue": "kaprien_internals",
            "acks_late": True,
        },
    },
    "publish_targets_meta": {
        "task": "app.kaprien_repo_worker",
        "schedule": schedules.crontab(minute="*/1"),
        "kwargs": {
            "action": "publish_targets_meta",
        },
        "options": {
            "task_id": "publish_targets_meta",
            "queue": "kaprien_internals",
            "acks_late": True,
        },
    },
}

repository = MetadataRepository.create_service()