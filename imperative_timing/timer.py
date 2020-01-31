import typing as ty

from time import monotonic, sleep
from contextlib import suppress
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support.wait import WebDriverWait, TimeoutException, NoSuchElementException


T = ty.TypeVar('T')
OneOrMany = ty.Union[T, ty.Iterable[T]]
FromWebDriver = ty.Callable[[WebDriver], T]


def drivefy(func, *args, **kwargs) -> FromWebDriver:
    """Create function with single stub argument that returns `func(*args, **kwargs)`.
    It may be useful for things like `wait.until(self.drivefy(func, *args, **kwargs))`"""

    def _mock(_: WebDriver):
        return func(*args, **kwargs)

    return _mock


class _Timer:
    def __init__(self, timeout: float, autostart: bool = False):
        self._last_start = 0
        self._last_timeout = timeout
        self._running = False
        if autostart:
            self.start()

    def start(self):
        self._running = True
        self._last_start = monotonic()

    def stop(self):
        self._running = False
        self._last_timeout = self.timeout()

    def timeout(self) -> float:
        if not self._running:
            return self._last_timeout
        return max(self._last_timeout - monotonic() + self._last_start, 0)

    @property
    def running(self):
        return self._running


class NormalWebDriverWait:
    """Like `WebDriverWait`, but unironically. Argument names in `__init__`
    fits what they really responsible for. Could wait multiple things at the save time.
    Time expires by default - when you pass `NormalWebDriverWait` instance to some function it implies
    it should complete not later than `countdown_sec` since instance creation"""

    def __init__(self, driver: WebDriver,
                 timeout: float,
                 min_poll_duration: float = 0,
                 ignore_exceptions: OneOrMany[Exception] = None,
                 eventually_expires=True):
        self._driver = driver
        self._timer = _Timer(timeout, autostart=eventually_expires)
        self._min_poll_duration = min_poll_duration

        self._ignored_exceptions = {NoSuchElementException}
        if ignore_exceptions is not None:
            if isinstance(ignore_exceptions, ty.Iterable):
                self._ignored_exceptions.update(ignore_exceptions)
            else:
                self._ignored_exceptions.add(ignore_exceptions)
        self._ignored_exceptions = tuple(self._ignored_exceptions)

    @classmethod
    def from_wait(cls, wait: WebDriverWait, eventually_expires=True):
        return cls(driver=wait._driver,
                   timeout=wait._timeout,
                   min_poll_duration=wait._poll,
                   ignore_exceptions=wait._ignored_exceptions,
                   eventually_expires=eventually_expires)

    @property
    def driver(self) -> WebDriver:
        return self._driver

    @property
    def min_poll_duration(self) -> float:
        return self._min_poll_duration

    @property
    def timeout(self) -> float:
        return self._timer.timeout()

    def _until_predicate(self, method: FromWebDriver[T],
                        predicate: ty.Callable[[T], bool], message: str = '') -> T:
        """Calls the method provided with the driver as an argument until the \
        return value not satisfied predicate."""
        screen = None
        stacktrace = None

        while True:
            poll_timer = _Timer(self._min_poll_duration, autostart=True)
            try:
                value = method(self._driver)
                if predicate(value):
                    return value
            except self._ignored_exceptions as exc:
                screen = getattr(exc, 'screen', None)
                stacktrace = getattr(exc, 'stacktrace', None)
            sleep(poll_timer.timeout())
            if self._timer.timeout() == 0:
                break
        raise TimeoutException(message, screen, stacktrace)

    def until(self, method: FromWebDriver, message: str = '') -> T:
        """Calls the method provided with the driver as an argument until the \
        return value not convertible to False."""
        return self._until_predicate(method, bool, message)

    def until_not(self, method: FromWebDriver, message: str = '') -> T:
        """Calls the method provided with the driver as an argument until the \
        return value convertible to False."""
        return self._until_predicate(method, lambda x: not x, message)

    def until_any(self, methods: ty.Iterable[FromWebDriver[T]]) -> T:
        return self.until(self._checker_any(methods))

    def _checker_any(self, methods: ty.Iterable[FromWebDriver[T]]) -> FromWebDriver[T]:
        def _check_any(driver):
            for method in methods:
                with suppress(self._ignored_exceptions):
                    result = method(driver)
                    if result:
                        return result
            return None
        return _check_any

    def spawn(self, max_timeout=None,
              min_poll_duration=None,
              ignored_exceptions=None,
              ):  # type: (float, float, OneOrMany[Exception]) -> NormalWebDriverWait
        timer = self._timer

        if max_timeout is None:
            timeout = self._timer.timeout()
        else:
            timeout = min(max_timeout, timer.timeout())

        if min_poll_duration is None:
            min_poll_duration = self._min_poll_duration

        if ignored_exceptions is None:
            ignored_exceptions = self._ignored_exceptions

        return self.__class__(driver=self._driver,
                              timeout=timeout,
                              min_poll_duration=min_poll_duration,
                              ignore_exceptions=ignored_exceptions,
                              eventually_expires=timer.running)

    def attempts(self):
        return AttemptSeries(self.timeout, self.min_poll_duration)


class AttemptSeries:
    def __init__(self, timeout: float, min_poll_duration: float = 0):
        self._timer_total = _Timer(timeout, autostart=True)
        self._min_poll_duration = min_poll_duration
        self._timer_poll = _Timer(0, autostart=True)
        self._stop_iteration = False
        self.result = None

    @property
    def timeout(self):
        return self._timer_total.timeout()

    def __iter__(self):
        self._stop_iteration = False
        return self

    def success(self, result=None):
        self._stop_iteration = True
        self.result = result

    def __next__(self):
        if self._stop_iteration:
            raise StopIteration

        sleep(self._timer_poll.timeout())
        self.timer_poll = _Timer(self._min_poll_duration, autostart=True)

        if self.timeout <= 0:
            raise TimeoutException

        return Attempt(self)


class Attempt:

    def __init__(self, series: AttemptSeries):
        self._series = series
        self._ignored_exceptions = tuple()
        self.result = None

    def suppress(self, *exceptions):  # type: (ty.Tuple[Exception]) -> Attempt
        self._ignored_exceptions = exceptions
        return self

    class Success(Exception):
        def __init__(self, owner, result):
            self.owner = owner
            self.result = result

    def _raise_success(self, result=None) -> ty.NoReturn:
        raise self.Success(self, result)

    def __enter__(self):
        self.result = None
        return self._raise_success

    def __exit__(self, exc_type, exc_val, exc_tb):
        if isinstance(exc_val, self.Success):
            if exc_val.owner is not self:
                return False
            self.result = exc_val.result
            self._series.success(exc_val.result)
            return True

        if isinstance(exc_val, self._ignored_exceptions):
            if self._series.timeout <= 0:
                raise TimeoutException from exc_val
            else:
                return True
