"""Barramento de Eventos (Observer Pattern) para observabilidade e desacoplamento."""
from typing import Callable, Dict, List, Any
from dataclasses import dataclass
from barsi.infra.logging import logger

@dataclass
class Event:
    name: str
    payload: Dict[str, Any]

class EventBus:
    def __init__(self):
        self._listeners: Dict[str, List[Callable[[Event], None]]] = {}

    def subscribe(self, event_name: str, listener: Callable[[Event], None]):
        if event_name not in self._listeners:
            self._listeners[event_name] = []
        self._listeners[event_name].append(listener)

    def publish(self, event_name: str, payload: Dict[str, Any]):
        event = Event(name=event_name, payload=payload)
        logger.debug("event_published", name=event_name, payload=payload)
        if event_name in self._listeners:
            for listener in self._listeners[event_name]:
                try:
                    listener(event)
                except Exception as exc:
                    logger.error("listener_error", event=event_name, error=str(exc))

event_bus = EventBus()

