from __future__ import annotations

from dataclasses import dataclass
import traceback
from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


@dataclass(slots=True)
class WorkerError:
    message: str
    exception_type: str
    traceback_text: str


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(object)
    finished = Signal()


class AsyncWorker(QRunnable):
    def __init__(self, task: Callable[[], object]) -> None:
        super().__init__()
        self.task = task
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.task()
        except Exception as exc:
            self.signals.error.emit(
                WorkerError(
                    message=str(exc) or exc.__class__.__name__,
                    exception_type=exc.__class__.__name__,
                    traceback_text=traceback.format_exc(),
                )
            )
        else:
            self.signals.result.emit(result)
        finally:
            self.signals.finished.emit()
