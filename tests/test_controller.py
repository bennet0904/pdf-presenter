import time

import pytest

from pdf_presenter.controller import PresentationController, format_duration


@pytest.fixture
def ctrl(qapp):
    return PresentationController(page_count=5)


def test_navigation_and_bounds(ctrl):
    seen = []
    ctrl.page_changed.connect(seen.append)
    ctrl.prev()
    assert ctrl.current_page == 0 and seen == []
    ctrl.next(); ctrl.next()
    assert ctrl.current_page == 2 and ctrl.next_page == 3
    ctrl.last()
    assert ctrl.current_page == 4 and ctrl.next_page is None
    ctrl.next()
    assert ctrl.current_page == 4
    ctrl.goto(-10)
    assert ctrl.current_page == 0
    assert seen == [1, 2, 4, 0]


def test_start_page_is_clamped(qapp):
    assert PresentationController(3, start_page=9).current_page == 2


def test_blank_screen_stays_while_navigating(ctrl):
    ctrl.toggle_black()
    assert ctrl.blank == "black"
    ctrl.next(); ctrl.next(); ctrl.goto(4)
    assert ctrl.blank == "black" and ctrl.current_page == 4
    ctrl.toggle_black()
    assert ctrl.blank == "" and ctrl.current_page == 4
    ctrl.toggle_white(); ctrl.toggle_white()
    assert ctrl.blank == ""


def test_timer_start_pause_reset(ctrl):
    assert ctrl.elapsed_ms() == 0 and not ctrl.timer_running
    ctrl.start_timer()
    time.sleep(0.05)
    ctrl.pause_timer()
    paused = ctrl.elapsed_ms()
    assert paused >= 40
    time.sleep(0.03)
    assert ctrl.elapsed_ms() == paused  # pausiert -> zählt nicht weiter
    ctrl.toggle_timer()
    assert ctrl.timer_running
    ctrl.reset_timer()
    assert ctrl.elapsed_ms() < 20 and ctrl.timer_running


def test_timer_keeps_running_while_blank(ctrl):
    ctrl.start_timer()
    ctrl.toggle_black()
    time.sleep(0.03)
    assert ctrl.elapsed_ms() >= 20


def test_format_duration():
    assert format_duration(0) == "00:00:00"
    assert format_duration(61_999) == "00:01:01"
    assert format_duration(3_723_000) == "01:02:03"
