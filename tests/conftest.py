"""Shared fixtures. The unit tests load bisik with macOS and audio frameworks replaced by mocks."""

import importlib
import sys
from unittest import mock

import pytest

FN_MASK = 0x800000
COMMAND_MASK = 0x100000
TAP_DISABLED_BY_TIMEOUT = 0xFFFFFFFE
TAP_DISABLED_BY_USER_INPUT = 0xFFFFFFFF


def make_quartz():
    quartz = mock.MagicMock(name="Quartz")
    quartz.kCGEventFlagMaskSecondaryFn = FN_MASK
    quartz.kCGEventFlagMaskCommand = COMMAND_MASK
    quartz.kCGEventTapDisabledByTimeout = TAP_DISABLED_BY_TIMEOUT
    quartz.kCGEventTapDisabledByUserInput = TAP_DISABLED_BY_USER_INPUT
    quartz.CGPreflightListenEventAccess.return_value = True
    return quartz


def load_bisik(monkeypatch, **replacements):
    """Import a fresh copy of bisik with the given modules replaced in sys.modules."""
    replacements.setdefault("Quartz", make_quartz())
    for name, module in replacements.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.delitem(sys.modules, "bisik", raising=False)
    return importlib.import_module("bisik")


@pytest.fixture
def bisik(monkeypatch):
    module = load_bisik(
        monkeypatch,
        mlx_whisper=mock.MagicMock(name="mlx_whisper"),
        sounddevice=mock.MagicMock(name="sounddevice"),
    )
    monkeypatch.delenv("BISIK_HUD", raising=False)
    return module
