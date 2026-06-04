from unittest.mock import patch
from confirmer import ask


def test_yes_answer():
    with patch("builtins.input", return_value="y"):
        assert ask("继续?") is True


def test_no_answer():
    with patch("builtins.input", return_value="n"):
        assert ask("继续?") is False


def test_empty_defaults_to_false():
    with patch("builtins.input", return_value=""):
        assert ask("继续?") is False


def test_empty_defaults_to_true_when_set():
    with patch("builtins.input", return_value=""):
        assert ask("继续?", default=True) is True


def test_yes_full_word():
    with patch("builtins.input", return_value="yes"):
        assert ask("继续?") is True


def test_eof_returns_false():
    with patch("builtins.input", side_effect=EOFError):
        assert ask("继续?") is False


def test_keyboard_interrupt_returns_false():
    with patch("builtins.input", side_effect=KeyboardInterrupt):
        assert ask("继续?") is False
