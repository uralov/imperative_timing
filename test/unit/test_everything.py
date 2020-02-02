from time import monotonic
from unittest.mock import Mock
import typing as ty
import pytest

from selenium.webdriver.support.wait import WebDriverWait
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from imperative_timing import NormalWebDriverWait, drivefy


class ActionMock:
    def __init__(self, timeout: float,
                 return_before: ty.Any,
                 return_after: ty.Any):
        self.timeout = timeout
        self.before = return_before
        self.after = return_after
        self.borderline = None

    def __call__(self, _):
        timestamp = monotonic()
        if self.borderline is None:
            self.borderline = timestamp + self.timeout
        ret = self.before if timestamp < self.borderline else self.after
        if isinstance(ret, Exception) or (isinstance(ret, type) and issubclass(ret, Exception)):
            raise ret
        return ret


@pytest.fixture
def driver_mock():
    return Mock()


def test_drivefy(driver_mock):
    wait = WebDriverWait(driver_mock, 1, 0.1)
    sentinel = object()
    def correct_signature(driver): return sentinel
    def no_args(): return sentinel
    def two_args(arg1, arg2): return arg2
    def with_kwargs(arg, *, kwarg): return kwarg
    assert wait.until(correct_signature) == sentinel

    with pytest.raises(TypeError):
        wait.until(no_args)
    with pytest.raises(TypeError):
        wait.until(two_args)
    with pytest.raises(TypeError):
        wait.until(with_kwargs)

    no_args = drivefy(no_args)
    assert wait.until(no_args) == sentinel

    two_args = drivefy(two_args, "mock", sentinel)
    assert wait.until(two_args) == sentinel

    with_kwargs = drivefy(with_kwargs, "mock", kwarg=sentinel)
    assert wait.until(with_kwargs) == sentinel


def test_timer_exhausting(driver_mock):
    wait = NormalWebDriverWait(driver_mock, 1, 0.1)
    webelement_mock = "WebElementMock"
    find_element_mocks = [ActionMock(0.4, NoSuchElementException, webelement_mock)
                          for _ in range(3)]
    assert wait.until(find_element_mocks[0]) == webelement_mock
    assert wait.until(find_element_mocks[1]) == webelement_mock
    with pytest.raises(TimeoutException):
        wait.until(find_element_mocks[2])


def test_wait_until(driver_mock):
    wait = NormalWebDriverWait(driver_mock, 1, 0.1)
    webelement_mock = "WebElementMock"
    find_element_mock = ActionMock(0.4, webelement_mock, NoSuchElementException)
    find_elements_mock = ActionMock(0.4, [webelement_mock], [])
    condition_mock = ActionMock(0.4, True, False)
    assert wait.until_not(find_element_mock) is True  # mimic WebDriverWait behavior
    assert wait.until_not(find_elements_mock) == []
    with pytest.raises(TimeoutException):
        wait.until_not(condition_mock)


@pytest.mark.parametrize("timeout, poll_duration, ignored_exceptions", [
    (timeout, poll_duration, ignored_exceptions)
    for timeout in (None, 0.5, 2)
    for poll_duration in (None, 0.2)
    for ignored_exceptions in (None, (FileNotFoundError, ValueError))
])
def test_spawn(driver_mock, timeout, poll_duration, ignored_exceptions):
    protowait = NormalWebDriverWait(driver_mock, 1, 0.1)
    wait = protowait.spawn(max_timeout=timeout, min_poll_duration=poll_duration,
                           ignored_exceptions=ignored_exceptions)

    expected_timeout = protowait.timeout \
        if timeout is None or timeout > protowait.timeout \
        else timeout
    assert wait.timeout == pytest.approx(expected_timeout, abs=0.05)

    expected_poll_duration = protowait.min_poll_duration \
        if poll_duration is None \
        else poll_duration
    assert wait.min_poll_duration == expected_poll_duration

    expected_ignored_exceptions = protowait._ignored_exceptions \
        if ignored_exceptions is None \
        else ignored_exceptions
    assert all(exc in wait._ignored_exceptions for exc in expected_ignored_exceptions)


def test_from_and_to_standart_wait(driver_mock):
    standart = WebDriverWait(driver_mock, 6, 1)
    normal = NormalWebDriverWait.from_standart_wait(standart, eventually_expires=False)
    standart2 = normal.to_standart_wait()
    assert standart._timeout == standart2._timeout
    assert standart._poll == standart2._poll
    assert set(standart._ignored_exceptions) == set(standart2._ignored_exceptions)


@pytest.mark.parametrize("mocks_timeouts", [
    (.4, 0, .1, 1, .9),
    (.3, .6, .9),
    (.5, 2, .8),
    (.7, .5, .4, .8),
    (5, 3, 4)
])
def test_wait_until_any(driver_mock, mocks_timeouts: tuple):
    total_timeout, poll_duration = 1, 0.05
    wait = NormalWebDriverWait(driver_mock, total_timeout, poll_duration)
    action_mocks = [ActionMock(timeout=mt, return_before=None, return_after=str(mt))
                    for mt in mocks_timeouts]
    min_timeout = min(mocks_timeouts)
    if min_timeout >= total_timeout:
        with pytest.raises(TimeoutException):
            wait.until_any(action_mocks)
    else:
        common_timeout = float(wait.until_any(action_mocks))
        assert common_timeout == pytest.approx(min_timeout, abs=poll_duration)


def test_attempts(driver_mock):
    total_timeout, poll_duration = 1, 0.05
    wait = NormalWebDriverWait(driver_mock, total_timeout, poll_duration)
    partial_timeout = wait.timeout / 5

    throw_no_element = ActionMock(timeout=partial_timeout,
                                  return_before=NoSuchElementException,
                                  return_after=None)
    for attempt in wait.attempts():
        with attempt.suppress(NoSuchElementException):
            throw_no_element(driver_mock)
            break
    expected_timeout = total_timeout - partial_timeout
    assert wait.timeout == pytest.approx(expected_timeout, abs=.05)

    throw_no_element = ActionMock(timeout=partial_timeout,
                                  return_before=NoSuchElementException,
                                  return_after=None)
    attempts = wait.attempts()
    for attempt in attempts:
        with attempt as success:
            success("success - 42")
            throw_no_element(driver_mock)
    assert wait.timeout == pytest.approx(expected_timeout, abs=.05)
    assert attempt.result == "success - 42" == attempts.result

    for attempt in wait.attempts():
        with attempt as success1:
            for subatempt in wait.attempts():
                with subatempt as success2:
                    success1(42)
    assert wait.timeout == pytest.approx(expected_timeout, abs=.05)
    assert attempt.result == 42
    assert subatempt.result is None

    with pytest.raises(TimeoutException):
        for attempt in wait.attempts():
            with attempt:
                pass
