import typing as ty

from time import monotonic, sleep
from contextlib import suppress
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support.wait import WebDriverWait, TimeoutException, NoSuchElementException


def drivefy(func, *args, **kwargs):
    """Create function with single stub argument that returns `func(*args, **kwargs)`.
    It may be useful for things like `wait.until(self.drivefy(func, *args, **kwargs))`"""

    def _mock(_: WebDriver):
        return func(*args, **kwargs)

    return _mock


class Timer:
    def __init__(self, wait: WebDriverWait):
        self._prototype = wait
        self._start = monotonic()

    @property
    def timeout(self) -> float:
        return max(self._prototype._timeout - monotonic() + self._start, 0)

    @property
    def driver(self) -> WebDriver:
        return self._prototype._driver

    @property
    def poll_duration(self) -> float:
        return self._prototype._poll

    @property
    def ignored_exceptions(self) -> ty.Tuple[Exception]:
        return self._prototype._ignored_exceptions

    def _spawn_proto(self, max_timeout: float = None,
                     poll_duration: float = None,
                     ignored_exceptions: ty.Tuple[Exception] = None) -> WebDriverWait:
        if max_timeout is None:
            max_timeout = self.timeout
        else:
            max_timeout = min(max_timeout, self.timeout)

        if poll_duration is None:
            poll_duration = self.poll_duration

        if ignored_exceptions is None:
            ignored_exceptions = set(self.ignored_exceptions) - {NoSuchElementException}

        return WebDriverWait(self.driver, max_timeout, poll_duration, ignored_exceptions)

    @property
    def wait(self) -> WebDriverWait:
        return self._spawn_proto()

    def wait_until(self, func, *args, **kwargs):
        """Like `.wait.until`, but repeating `func(*args, **kwargs)`
        until result could be converted to `True`"""
        return self.wait.until(drivefy(func, *args, **kwargs))

    def wait_until_not(self, func, *args, **kwargs):
        """Like `.wait.until_not`, but repeating `func(*args, **kwargs)`
        until result could be converted to `True`"""
        return self.wait.until_not(drivefy(func, *args, **kwargs))

    def wait_until_any(self, methods):
        return self.wait.until(self._checker_any(methods))

    def _checker_any(self, methods):
        def _check_any(driver):
            for method in methods:
                with suppress(self.ignored_exceptions):
                    result = method(driver)
                    if result:
                        return result
            return None
        return _check_any

    def spawn(self, max_timeout=None,
              poll_duration=None,
              ignored_exceptions=None):
        return self.__class__(self._spawn_proto(max_timeout, poll_duration, ignored_exceptions))

    def attempt(self):
        return _Attempt(self)


class _Attempt:
    def __init__(self, timer: Timer):
        self.timer_total = timer
        self.timer_poll = self.timer_total.spawn(max_timeout=0)
        self._stop_iteration = False
        self.result = None

    def __iter__(self):
        self._stop_iteration = False
        return self

    def __next__(self):
        if self._stop_iteration:
            raise StopIteration

        self._finalize_poll()

        if self.timer_total.timeout <= 0:
            raise TimeoutException

        return self

    def _finalize_poll(self):
        sleep(self.timer_poll.timeout)
        poll_duration = self.timer_total.poll_duration
        self.timer_poll = self.timer_total.spawn(max_timeout=poll_duration)

    class Success(Exception):
        def __init__(self, result):
            self.result = result

    class Failure(Exception):
        pass

    @classmethod
    def raise_success(cls, result=None):
        raise cls.Success(result)

    @classmethod
    def raise_failure(cls):
        raise cls.Failure

    def __enter__(self):
        self.result = None
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_val is None:
            self._stop_iteration = True
            return True

        if isinstance(exc_val, self.Success):
            self.result = exc_val.result
            self._stop_iteration = True
            return True

        if isinstance(exc_val, self.Failure):
            return True

        if isinstance(exc_val, TimeoutException):
            if self.timer_total.timeout <= 0:
                raise TimeoutException from exc_val
            else:
                return True

        if isinstance(exc_val, self.timer_total.ignored_exceptions):
            return True
