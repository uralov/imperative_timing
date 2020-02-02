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
    def from_standart_wait(cls, wait: WebDriverWait, eventually_expires=True):
        return cls(driver=wait._driver,
                   timeout=wait._timeout,
                   min_poll_duration=wait._poll,
                   ignore_exceptions=wait._ignored_exceptions,
                   eventually_expires=eventually_expires)

    def to_standart_wait(self) -> WebDriverWait:
        poll_duration = self.min_poll_duration
        if poll_duration == 0:
            # standart WebDriverWait replaces 0 to 0.5 in order to "avoid the divide by zero"
            poll_duration = 1e-10
        return WebDriverWait(self.driver, self.timeout, poll_duration, self._ignored_exceptions)

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
                if predicate(None):
                    return True  # it's True just in order to copy WebDriverWait behavior
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
        """Calls specified methods provided with driver as an argument until
        one of them return value not convertible to False"""
        return self.until(self._checker_any(methods))

    def _checker_any(self, methods: ty.Iterable[FromWebDriver[T]]) -> FromWebDriver[T]:
        """Wraps methods methods provided with driver as an argument in a single such method"""
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
        """Creates new instance of `NormalWebDriverWait`, substituting specified parameters
        and copies unspecified from prototype instance"""
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
        """Convenient way to create `AttemptSeries` instance with `timeout` and
        `min_poll_duration` like in current `NormalWebDriverWait` instance"""
        return AttemptSeries(self.timeout, self.min_poll_duration)


class AttemptSeries:
    """Allows to iterate over infinite `Attempt` instances until `timeout` expires or
    `Attempt` or `AttemptSeries` instance "succeed". Standart usage:

    `for attempt in wait.attempts():
        with attempt as success:
            success(42)`

    `success` raises special exception that would caught by `attempt` that created `success`,
    and stops infinite iteration of attempt. `Attempt.result` would hold value `42`
    or any other value passed to `success()`. If there a nested contexts exception would
    still be caught by "parent" `attempt`:
    `
    >>> series = AttemptSeries(1, 0.1)
    >>> subseries = AttemptSeries(0.5, 0.1)
    >>> for attempt in series:
    ...     with attempt as success1:
    ...         for subatempt in subseries:
    ...             with subatempt as success2:
    ...                 success1("123")
    >>> assert attempt.result == "123" == series.result
    >>> assert subatempt.result is None is subseries.result

    `

    If attempt not able to succeed in `timeout`
    `selenium.common.exceptions.TimeoutException` would be raised.
    Examples below are sophisticated and computationally expensive way
    to wait a second and throw `TimeoutException`:
    `
    >>> for _ in AttemptSeries(1):  # doctest: +ELLIPSIS
    ...     pass
    Traceback (most recent call last):
        ...
    selenium.common.exceptions.TimeoutException: ...

    `

    `
    >>> for attempt in AttemptSeries(1):  # doctest: +ELLIPSIS
    ...    with attempt as _:
    ...        pass
    Traceback (most recent call last):
        ...
    selenium.common.exceptions.TimeoutException: ...

    `
    """
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
    """Explained in `AttemptSeries`. Additionally, `Attempt` instance could
    set exceptions that would be suppresed like `contextlib.suppress`
    `
    >>> for attempt in AttemptSeries(1):  # doctest: +ELLIPSIS
    ...     with attempt.suppress(NoSuchElementException) as _:
    ...         raise NoSuchElementException
    Traceback (most recent call last):
        ...
    selenium.common.exceptions.TimeoutException: ...
    <BLANKLINE>

    `
    """
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

