import threading
import time
from typing import Callable, Any


class TimeoutError(Exception):
    """Raised when an operation times out."""
    pass


def invoke_with_retry(llm, prompt: str, idle_timeout_seconds: int, max_retries: int) -> str:
    """
    Invoke LLM with idle timeout and retry logic.

    Idle timeout means: if no tokens are generated for idle_timeout_seconds, abort.
    This is different from a hard timeout—allows long generations to complete.
    """
    last_error = None

    for attempt in range(max_retries):
        try:
            # Use streaming to detect idle (no tokens for X seconds)
            result = []
            last_token_time = [time.time()]

            def stream_handler(chunk):
                """Track token generation time."""
                last_token_time[0] = time.time()
                result.append(chunk)

            # Try streaming first if available
            try:
                for chunk in llm.stream(prompt):
                    current_time = time.time()
                    if current_time - last_token_time[0] > idle_timeout_seconds:
                        raise TimeoutError(f"No tokens generated for {idle_timeout_seconds} seconds (idle timeout)")
                    result.append(chunk)
                    last_token_time[0] = current_time

                response = "".join(result) if result else llm.invoke(prompt)
            except (AttributeError, TypeError):
                # Fallback: if streaming not available, use invoke with hard timeout
                response = _invoke_with_hard_timeout(llm, prompt, idle_timeout_seconds * 2)

            return response.strip()

        except TimeoutError as e:
            last_error = f"Idle timeout (attempt {attempt + 1}/{max_retries}): {str(e)}"
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                time.sleep(wait_time)
            continue
        except Exception as e:
            last_error = f"Error (attempt {attempt + 1}/{max_retries}): {str(e)}"
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                time.sleep(wait_time)
            continue

    raise TimeoutError(f"Failed after {max_retries} attempts: {last_error}")


def _invoke_with_hard_timeout(llm, prompt: str, timeout_seconds: int) -> str:
    """Fallback: hard timeout using threading."""
    result = [None]
    exception = [None]

    def target():
        try:
            result[0] = llm.invoke(prompt)
        except Exception as e:
            exception[0] = e

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout=timeout_seconds)

    if thread.is_alive():
        raise TimeoutError(f"Hard timeout after {timeout_seconds} seconds (no streaming available)")

    if exception[0]:
        raise exception[0]

    return result[0]
