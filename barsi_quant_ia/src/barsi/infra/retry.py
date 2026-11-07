"""Políticas de Retry resiliente com backoff exponencial e jitter."""
from tenacity import retry, stop_after_attempt, wait_exponential_jitter, retry_if_exception_type
import httpx

network_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=1.0, max=6.0, jitter=0.5),
    retry=retry_if_exception_type((httpx.RequestError, httpx.TimeoutException, ConnectionError, TimeoutError)),
    reraise=True
)

