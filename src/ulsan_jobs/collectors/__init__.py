from .afschool import AfschoolCollector
from .base import Collector, NotConfigured
from .board import BoardCollector, FormBoardCollector
from .json_board import JsonBoardCollector
from .work24_api import Work24Collector
from .work24_web import Work24WebCollector

# sources.yaml 의 collector 이름 → 수집기
COLLECTORS: dict[str, type[Collector]] = {
    "generic_board": BoardCollector,
    "use_cms": BoardCollector,
    "form_board": FormBoardCollector,
    "afschool": AfschoolCollector,
    "json_board": JsonBoardCollector,
    "work24_api": Work24Collector,
    "work24_web": Work24WebCollector,
}

__all__ = ["COLLECTORS", "Collector", "NotConfigured"]
